import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core.config import settings
from app.services.catalog_service import CatalogService
from app.services.ekt_client import EKTClient, EKTAPIError


class SyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [patch.object(settings, 'data_dir', Path(self.directory.name)), patch.object(settings, 'catalog_search_max_pages', 2)]
        for p in self.patches:
            p.start()
        self.client = EKTClient()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    async def test_resume_after_restart_until_empty_page(self):
        self.client.get_products = AsyncMock(side_effect=lambda page: [{'id': page, 'name': str(page)}] if page <= 3 else [])
        service = CatalogService(self.client)
        await service._load_live_catalog(advance=True)
        self.assertFalse(service.coverage['complete'])
        self.assertEqual(service.coverage['next_page'], 3)
        service = CatalogService(self.client)
        await service._load_live_catalog(advance=True)
        self.assertTrue(service.coverage['complete'])
        self.assertEqual([p['id'] for p in service._catalog], [1, 2, 3])
        self.assertEqual([c.args[0] for c in self.client.get_products.call_args_list], [1, 2, 3, 4])

    async def test_failure_keeps_snapshot_and_cursor(self):
        self.client.get_products = AsyncMock(side_effect=lambda page: [{'id': page}])
        service = CatalogService(self.client)
        await service._load_live_catalog(advance=True)
        self.client.get_products = AsyncMock(side_effect=EKTAPIError('offline'))
        result = await service._load_live_catalog(advance=True)
        self.assertEqual(len(result), 2)
        self.assertTrue(service.coverage['stale'])
        self.assertEqual(service.coverage['next_page'], 3)

    async def test_repeated_page_is_not_full_catalog(self):
        self.client.get_products = AsyncMock(return_value=[{'id': 1}])
        service = CatalogService(self.client)
        await service._load_live_catalog(advance=True)
        self.assertFalse(service.coverage['complete'])
        self.assertTrue(service.coverage['stale'])
