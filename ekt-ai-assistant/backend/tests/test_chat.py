import importlib
import unittest
import tempfile
from pathlib import Path
from io import BytesIO
from types import SimpleNamespace
from PIL import Image
from unittest.mock import AsyncMock
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.services.catalog_service import CatalogService
from app.services.demo_catalog import DemoClient

module = importlib.import_module('app.api.chat')

class ChatTests(unittest.TestCase):
    def setUp(self):
        from app.main import ip_requests
        ip_requests.clear()
        module.sessions.clear()
        self.directory = tempfile.TemporaryDirectory()
        self.data_patch = patch.object(module.settings, "data_dir", Path(self.directory.name))
        self.data_patch.start()
        self.patch = patch.object(module, 'catalog', CatalogService(DemoClient()))
        self.patch.start()
        self.mode_patch = patch.object(module.settings, 'demo_mode', True)
        self.mode_patch.start()
        self.ai_patch = patch.object(module.settings, 'ai_dialogue', False)
        self.ai_patch.start()
        self.client = TestClient(app).__enter__()
        self.csrf = self.client.get('/api/session').json()['csrf']

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.data_patch.stop()
        self.directory.cleanup()
        self.ai_patch.stop()
        self.mode_patch.stop()
        self.patch.stop()

    def post(self, path, body):
        return self.client.post(path, json=body, headers={'X-CSRF-Token': self.csrf})

    def proposal(self, quantity=2):
        return self.post('/api/cart/propose', {'product_id': 2, 'quantity': quantity})

    def test_existing_article_has_stock_specs_certificate_and_analog(self):
        response = self.post('/api/chat', {'message': 'Есть DEMO-001?'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['products'][0]['quantity'], 0)
        self.assertEqual(data['products'][0]['characteristics']['Полюса'], '3')
        self.assertTrue(data['products'][0]['certificates'])
        self.assertEqual(data['alternatives'][0]['product']['id'], 2)
        self.assertIn('160 А', data['alternatives'][0]['reason'])
        self.assertEqual(self.client.get('/static/demo-certificate.txt').status_code, 200)

    def test_explicit_confirmation_replay_and_cart_link(self):
        proposal = self.proposal().json()
        self.assertEqual(self.client.get('/api/cart').json()['items'], [])
        self.post('/api/chat', {'message': 'может быть'})
        self.assertEqual(self.client.get('/api/cart').json()['items'], [])
        body = {'proposal_id': proposal['proposal_id'], 'confirmed': True}
        result = self.post('/api/cart/confirm', body)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['items'][0]['quantity'], 2)
        self.assertEqual(self.client.get(result.json()['cart_url']).status_code, 200)
        self.assertEqual(self.post('/api/cart/confirm', body).status_code, 409)

    def test_overstock_and_combined_quantity(self):
        self.assertEqual(self.proposal(13).status_code, 409)
        p = self.proposal(10).json()
        self.post('/api/cart/confirm', {'proposal_id': p['proposal_id'], 'confirmed': True})
        self.assertEqual(self.proposal(3).status_code, 409)
        self.assertEqual(self.proposal(-1).status_code, 422)

    def test_stock_is_rechecked(self):
        p = self.proposal(10).json()
        original = module.catalog.client.get_product_detail
        async def changed(product_id):
            data = await original(product_id)
            data['quantity'] = 1
            return data
        with patch.object(module.catalog.client, 'get_product_detail', changed):
            self.assertEqual(self.post('/api/cart/confirm', {'proposal_id': p['proposal_id'], 'confirmed': True}).status_code, 409)
        self.assertEqual(self.client.get('/api/cart').json()['items'], [])

    def test_csrf_session_isolation_and_delete(self):
        p = self.proposal().json()
        self.assertEqual(self.client.post('/api/cart/confirm', json={'proposal_id':p['proposal_id'],'confirmed':True}).status_code, 403)
        with TestClient(app) as other:
            self.assertEqual(other.get('/api/cart').status_code, 401)
            token=other.get('/api/session').json()['csrf']
            result=other.post('/api/cart/confirm',json={'proposal_id':p['proposal_id'],'confirmed':True},headers={'X-CSRF-Token':token})
            self.assertEqual(result.status_code,409)
        self.client.delete('/api/session',headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(self.client.get('/api/cart').status_code,401)

    def test_cancel_expiry_context_and_payment_data(self):
        p=self.proposal().json()
        self.post('/api/cart/confirm',{'proposal_id':p['proposal_id'],'confirmed':False})
        self.assertEqual(self.client.get('/api/cart').json()['items'],[])
        p=self.proposal().json()
        next(iter(module.sessions.values())).pending['expires']=0
        self.assertEqual(self.post('/api/cart/confirm',{'proposal_id':p['proposal_id'],'confirmed':True}).status_code,409)
        self.post('/api/chat',{'message':'DEMO-002'})
        self.assertEqual(self.post('/api/chat',{'message':'А характеристики?'}).json()['products'][0]['id'],2)
        self.assertIn('платёжные', self.post('/api/chat',{'message':'4111 1111 1111 1111'}).json()['answer'])

    def test_terms_and_no_silent_real_conditions(self):
        with patch.object(module.settings,'demo_mode',True):
            self.assertIn('Демонстрационные',self.post('/api/chat',{'message':'Оплата и доставка'}).json()['answer'])
        with patch.object(module.settings,'demo_mode',False), patch.object(module.settings,'purchase_terms',''):
            result = self.post('/api/chat',{'message':'Минимальная партия'}).json()
            self.assertIn('Минимальная партия зависит', result['answer'])
            self.assertEqual(result['sources'][0]['url'], 'https://ekt.kz/checkout-delivery/')

    def test_exact_chat_confirmation(self):
        self.proposal(1)
        self.assertEqual(self.post('/api/chat',{'message':'да, добавь'}).json()['items'][0]['quantity'],1)

    def test_image_validation(self):
        response=self.client.post('/api/chat/attachment',files={'file':('image.jpg',b'not an image','image/jpeg')},headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(response.status_code,400)

    def test_photo_through_chat_with_mocked_ai_never_changes_cart(self):
        image=Image.new('RGB',(20,20),'white')
        output=BytesIO()
        image.save(output,format='JPEG')
        ai=SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(output_text='DEMO-002'))),close=AsyncMock())
        from app.models.specification import SpecificationParseResult, SpecificationItem
        parsed=SpecificationParseResult(items=[SpecificationItem(raw_text='DEMO-002',search_query='DEMO-002',article='DEMO-002',quantity=2)])
        with patch('app.services.vision.get_openai_client',return_value=ai), patch.object(module,'parse_specification_text',AsyncMock(return_value=parsed)):
            result=self.client.post('/api/chat/attachment',files={'file':('product.jpg',output.getvalue(),'image/jpeg')},headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['items'][0]['products'][0]['id'],2)
        self.assertEqual(self.client.get('/api/cart').json()['items'],[])
        self.assertFalse(ai.responses.create.call_args.kwargs['store'])

    def test_confirmation_requires_boolean_and_conflicting_analog_rejected(self):
        p=self.proposal().json()
        self.assertEqual(self.post('/api/cart/confirm',{'proposal_id':p['proposal_id'],'confirmed':'yes'}).status_code,422)
        original=module.catalog.client.get_product_detail
        async def incompatible(product_id):
            data=await original(product_id)
            if product_id == 2:
                data['properties']['Номинальный ток']='250 А'
            return data
        with patch.object(module.catalog.client,'get_product_detail',incompatible):
            response=self.post('/api/chat',{'message':'DEMO-001'}).json()
        self.assertEqual(response['alternatives'],[])

    def test_script_and_security_headers(self):
        response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertIn("script-src 'self'",response.headers['Content-Security-Policy'])
        self.assertEqual(self.client.get('/static/app.js').status_code,200)
