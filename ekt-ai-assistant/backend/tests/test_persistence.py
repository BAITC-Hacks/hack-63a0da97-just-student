import time
import unittest

from tests import test_features as fixtures
from app.services import session_store

module = fixtures.module


class PersistenceTests(unittest.TestCase):
    setUp = fixtures.FeatureTests.setUp
    tearDown = fixtures.FeatureTests.tearDown
    post = fixtures.FeatureTests.post

    def test_restart_preserves_cart_history_and_consumed_confirmation(self):
        self.post('/api/chat', {'message': 'привет'})
        proposal = self.post('/api/cart/propose', {'product_id': 2, 'quantity': 2}).json()
        body = {'proposal_id': proposal['proposal_id'], 'confirmed': True}
        self.assertEqual(self.post('/api/cart/confirm', body).status_code, 200)
        module.sessions.clear()
        self.assertEqual(self.client.get('/api/session').json()['csrf'], self.token)
        self.assertEqual(self.client.get('/api/cart').json()['items'][0]['quantity'], 2)
        self.assertEqual(len(self.client.get('/api/history').json()['messages']), 2)
        self.assertEqual(self.post('/api/cart/confirm', body).status_code, 409)

    def test_logout_cannot_be_restored_or_resurrected(self):
        key = self.client.cookies.get('ekt_session')
        old = module.sessions[key]
        self.client.delete('/api/session', headers={'X-CSRF-Token': self.token})
        module.persist(old)
        self.assertIsNone(session_store.load(key))
        self.client.cookies.set('ekt_session', key)
        module.sessions.clear()
        self.assertEqual(self.client.get('/api/cart').status_code, 401)

    def test_expired_session_is_not_restored(self):
        key = self.client.cookies.get('ekt_session')
        value = module.sessions[key]
        value.expires = time.time() - 1
        module.persist(value)
        module.sessions.clear()
        self.assertEqual(self.client.get('/api/cart').status_code, 401)
        self.assertNotEqual(self.client.get('/api/session').json()['csrf'], self.token)

    def test_account_delete_revokes_persisted_sessions(self):
        result = self.post('/api/account/register', {'username': 'persist-user', 'password': 'test-password-123'}).json()
        key = self.client.cookies.get('ekt_session')
        self.token = result['csrf']
        module.sessions.clear()
        self.assertEqual(self.client.delete('/api/account', headers={'X-CSRF-Token': self.token}).status_code, 200)
        self.assertIsNone(session_store.load(key))
