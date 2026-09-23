import asyncio
import importlib
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from openpyxl import Workbook
from PIL import Image
from app.main import app, ip_requests
from app.core.config import settings
from app.services import accounts
from app.services.attachment_service import AttachmentError, extract_attachment
from app.services.catalog_service import CatalogService, _to_catalog_product
from app.services.demo_catalog import DemoClient
from app.services.pdf_ocr import extract_pdf_with_ocr
from app.services.specification_parser import parse_specification_text
from app.services.recommendations import reason

module = importlib.import_module('app.api.chat')

class FeatureTests(unittest.TestCase):
    def setUp(self):
        module.sessions.clear()
        ip_requests.clear()
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [patch.object(settings,'data_dir',Path(self.directory.name)), patch.object(module,'catalog',CatalogService(DemoClient())), patch.object(settings,'demo_mode',True)]
        for p in self.patches:
            p.start()
        self.client=TestClient(app).__enter__()
        self.token=self.client.get('/api/session').json()['csrf']

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def post(self,path,body):
        return self.client.post(path,json=body,headers={'X-CSRF-Token':self.token})

    def test_kazakh_dialogue_confirmation_and_terms(self):
        self.assertIn('Сәлем',self.post('/api/chat',{'message':'сәлем','language':'kk'}).json()['answer'])
        self.assertIn('Демо',self.post('/api/chat',{'message':'төлем','language':'kk'}).json()['answer'])
        self.post('/api/cart/propose',{'product_id':2,'quantity':1})
        response=self.post('/api/chat',{'message':'иә, қос','language':'kk'}).json()
        self.assertIn('себетіне',response['answer'])
        self.assertEqual(response['items'][0]['quantity'],1)

    def test_history_account_rotation_persistence_and_deletion(self):
        credentials={'username':'tester','password':'safe-test-password-42'}
        old=self.token
        result=self.post('/api/account/register',credentials)
        self.assertEqual(result.status_code,200)
        self.token=result.json()['csrf']
        self.assertNotEqual(old,self.token)
        self.post('/api/chat',{'message':'DEMO-002'})
        history=self.client.get('/api/history').json()
        self.assertTrue(history['saved'])
        self.assertEqual(history['messages'][0]['text'],'DEMO-002')
        with accounts.database() as db:
            stored=db.execute('SELECT password FROM users').fetchone()[0]
        self.assertNotEqual(stored,credentials['password'])
        module.sessions.clear()  # Simulate process restart: history is in SQLite.
        self.token=self.client.get('/api/session').json()['csrf']
        result=self.post('/api/account/login',credentials)
        self.token=result.json()['csrf']
        self.assertEqual(result.json()['history'][0]['text'],'DEMO-002')
        self.assertEqual(self.post('/api/chat',{'message':'4111 1111 1111 1111'}).status_code,200)
        self.assertNotIn('4111',str(self.client.get('/api/history').json()))
        self.assertEqual(self.client.delete('/api/account',headers={'X-CSRF-Token':self.token}).status_code,200)
        self.assertEqual(self.client.get('/api/history').status_code,401)
        with accounts.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM history').fetchone()[0],0)

    def test_failed_login_and_validation_never_echo_password(self):
        response=self.post('/api/account/login',{'username':'missing','password':'unique-private-test-password'})
        self.assertEqual(response.status_code,400)
        self.assertNotIn('unique-private',response.text)
        response=self.post('/api/account/register',{'username':'x','password':'secret'})
        self.assertEqual(response.status_code,422)
        self.assertNotIn('secret',response.text)

    def test_csrf_on_legacy_attachment_and_history(self):
        response=self.client.post('/api/attachments/extract',files={'file':('a.pdf',b'test')})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.client.delete('/api/history').status_code,403)

    def test_minimum_and_step_are_enforced(self):
        original=module.catalog.client.get_product_detail
        async def limits(product_id):
            p=await original(product_id)
            p.update(minimum_quantity=2,quantity_step=2)
            return p
        with patch.object(module.catalog.client,'get_product_detail',limits):
            self.assertEqual(self.post('/api/cart/propose',{'product_id':2,'quantity':1}).status_code,409)
            self.assertEqual(self.post('/api/cart/propose',{'product_id':2,'quantity':3}).status_code,409)
            self.assertEqual(self.post('/api/cart/propose',{'product_id':2,'quantity':4}).status_code,200)

    def test_price_change_invalidates_confirmation(self):
        proposal=self.post('/api/cart/propose',{'product_id':2,'quantity':1}).json()
        original=module.catalog.client.get_product_detail
        async def changed(product_id):
            p=await original(product_id);p['price']+=100
            return p
        with patch.object(module.catalog.client,'get_product_detail',changed):
            response=self.post('/api/cart/confirm',{'proposal_id':proposal['proposal_id'],'confirmed':True})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.client.get('/api/cart').json()['items'],[])

    def test_related_items_are_explicit_and_handoff_is_download_only(self):
        data=self.post('/api/chat',{'message':'DEMO-002'}).json()
        self.assertEqual(data['related'][0]['product']['id'],4)
        self.assertIn('демонстрационном',data['related'][0]['reason'])
        result=self.post('/api/chat',{'message':'менеджер'}).json()
        response=self.client.get(result['handoff_url'])
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response.headers['Content-Disposition'])
        self.assertIn('DEMO-002',response.text)

    def test_rate_limiting_survives_new_sessions(self):
        # All requests share the direct socket IP; creating sessions cannot reset it.
        for _ in range(119):
            self.client.get('/api/session')
        self.assertEqual(self.client.get('/api/session').status_code,429)

class ExtractionAndDataTests(unittest.TestCase):
    def test_oversized_workbook_is_not_silently_truncated(self):
        workbook=Workbook()
        for n in range(1001):
            workbook.active.append([n])
        output=BytesIO();workbook.save(output);workbook.close()
        with self.assertRaises(AttachmentError):
            extract_attachment('large.xlsx',output.getvalue())

    def test_long_document_is_processed_in_chunks(self):
        response=SimpleNamespace(output_text='{"items":[],"unresolved":["review"]}')
        client=SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=response)),close=AsyncMock())
        with patch('app.services.specification_parser.get_openai_client',return_value=client):
            result=asyncio.run(parse_specification_text(('item\n'*5000)))
        self.assertEqual(len(result.unresolved),2)
        self.assertEqual(client.responses.create.await_count,2)

    def test_scanned_pdf_ocr_pipeline(self):
        import pymupdf
        image=Image.new('RGB',(100,100),'white');output=BytesIO();image.save(output,'PNG')
        doc=pymupdf.open();page=doc.new_page();page.insert_image(page.rect,stream=output.getvalue());data=doc.tobytes();doc.close()
        with patch('app.services.pdf_ocr.extract_image',AsyncMock(return_value={'text':'DEMO-002 2 шт'})):
            result=asyncio.run(extract_pdf_with_ocr('scan.pdf',data))
        self.assertIn('DEMO-002',result['text'])
        self.assertFalse(result['truncated'])

    def test_category_from_url_is_labelled_and_minimum_is_mapped(self):
        p=_to_catalog_product({'id':1,'name':'test','url':'https://ekt.kz/catalog/breakers/series/product/','properties':{'KRATNOST_MIN':'5'}})
        self.assertEqual(p.category,'breakers/series')
        self.assertEqual(p.category_source,'product_url')
        self.assertEqual(p.minimum_quantity,5)

    def test_marketing_properties_do_not_block_technical_analog(self):
        a=_to_catalog_product({'id':1,'name':'A','category':'breakers','properties':{'Номинальный ток':'16 А','Полюса':'3','BRAND_PRIORITY':'1'}})
        b=_to_catalog_product({'id':2,'name':'B','category':'breakers','properties':{'Номинальный ток':'16 A','Полюса':'3','BRAND_PRIORITY':'7'}})
        self.assertIsNotNone(reason(a,b))

    def test_decimal_current_is_not_equal_to_integer_current(self):
        a=_to_catalog_product({'id':1,'name':'A','category':'breakers','properties':{'Номинальный ток':'1,6 А','Полюса':'3'}})
        b=_to_catalog_product({'id':2,'name':'B','category':'breakers','properties':{'Номинальный ток':'16 А','Полюса':'3'}})
        self.assertIsNone(reason(a,b))

    def test_certificate_url_does_not_use_name_or_unsafe_scheme(self):
        p=_to_catalog_product({'id':1,'name':'A','certificates':[{'name':'Certificate','url':'https://ekt.kz/cert.pdf'},'javascript:alert(1)','certificate label']})
        self.assertEqual(p.certificates,['https://ekt.kz/cert.pdf'])
