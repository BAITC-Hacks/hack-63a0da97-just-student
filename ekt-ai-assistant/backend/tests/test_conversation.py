import unittest
from unittest.mock import AsyncMock, patch
from app.services.dialogue import Plan
from app.services.specification_parser import SpecificationParserError
from tests import test_features as fixtures

module = fixtures.module


class ConversationTests(unittest.TestCase):
    setUp = fixtures.FeatureTests.setUp
    tearDown = fixtures.FeatureTests.tearDown
    post = fixtures.FeatureTests.post

    def converse(self, message, plan, explanation=None):
        with patch.object(module.settings, 'ai_dialogue', True), patch.object(module.settings, 'openai_api_key', 'test-placeholder'), patch.object(module, 'interpret', AsyncMock(return_value=plan)) as planner, patch.object(module, 'explain', AsyncMock(return_value=explanation or 'Проверенные данные ниже.')) as writer:
            response = self.post('/api/chat', {'message': message, 'language': 'ru'})
        return response, planner, writer

    def test_conversation_is_not_catalog_search(self):
        plan = Plan(intent='conversation', query='', reply='Давайте разберёмся. Что сейчас вызывает сложность?')
        with patch.object(module, 'search', AsyncMock()) as search:
            response, _, writer = self.converse('Я запутался и не знаю с чего начать', plan)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Давайте', response.json()['answer'])
        search.assert_not_awaited()
        writer.assert_not_awaited()
        self.assertEqual(self.client.get('/api/cart').json()['items'], [])

    def test_followup_passes_history_and_does_not_treat_order_number_as_sku(self):
        self.converse('Меня зовут Алия', Plan(intent='conversation', query='', reply='Здравствуйте, Алия!'))
        response, planner, _ = self.converse('Мой заказ 123456 задержался', Plan(intent='conversation', query='', reply='Понимаю, это неудобно. У меня нет доступа к статусу заказов.'))
        self.assertIn('Алия', str(planner.call_args.args[1]))
        self.assertEqual(response.json()['intent'], 'conversation')

    def test_product_answer_uses_fresh_facts(self):
        response, _, writer = self.converse('Нужны два автомата DEMO-002', Plan(intent='search', query='DEMO-002', reply='', quantity=2), 'В выбранной карточке есть остаток. Проверьте параметры перед выбором.')
        self.assertEqual(response.json()['requested_quantity'], 2)
        facts = writer.call_args.args[2]
        self.assertEqual(facts['products'][0]['id'], 2)
        self.assertEqual(facts['products'][0]['quantity'], 12)
        self.assertEqual(self.client.get('/api/cart').json()['items'], [])

    def test_writer_failure_preserves_cards(self):
        plan = Plan(intent='search', query='DEMO-002', reply='')
        with patch.object(module.settings, 'ai_dialogue', True), patch.object(module.settings, 'openai_api_key', 'test-placeholder'), patch.object(module, 'interpret', AsyncMock(return_value=plan)), patch.object(module, 'explain', AsyncMock(side_effect=SpecificationParserError('unavailable'))):
            result = self.post('/api/chat', {'message': 'Подбери DEMO-002'}).json()
        self.assertTrue(result['ai_fallback'])
        self.assertEqual(result['products'][0]['id'], 2)

    def test_ai_failure_is_not_false_no_products(self):
        with patch.object(module.settings, 'ai_dialogue', True), patch.object(module.settings, 'openai_api_key', 'test-placeholder'), patch.object(module, 'interpret', AsyncMock(side_effect=SpecificationParserError('unavailable'))):
            result = self.post('/api/chat', {'message': 'Спасибо за помощь'}).json()
        self.assertTrue(result['ai_fallback'])
        self.assertIn('временно недоступен', result['answer'])

    def test_secret_never_sent_to_ai_or_saved(self):
        with patch.object(module, 'interpret', AsyncMock()) as planner:
            result = self.post('/api/chat', {'message': 'sk-test-' + 'x' * 24}).json()
        planner.assert_not_awaited()
        self.assertIn('API-ключи', result['answer'])
        self.assertEqual(self.client.get('/api/history').json()['messages'], [])

    def test_confirmation_bypasses_ai(self):
        self.post('/api/cart/propose', {'product_id': 2, 'quantity': 1})
        with patch.object(module.settings, 'ai_dialogue', True), patch.object(module, 'interpret', AsyncMock()) as planner:
            result = self.post('/api/chat', {'message': 'да, добавь'}).json()
        planner.assert_not_awaited()
        self.assertEqual(result['items'][0]['quantity'], 1)
