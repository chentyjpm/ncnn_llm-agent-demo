"""Installer policy fixtures; real frozen executables run in the packaging workflow."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from local_agent.desktop import automatic_config, InstanceLock
from local_agent.model_hub import ModelHub, safe_name, digest, save_json
from local_agent.documents import DocumentTools, validate_office
from local_agent.paths import Workspace, PolicyError


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.engines = {k: str(self.home/k) for k in ('llm','image')}
        for name in self.engines.values(): Path(name).write_text('test marker, not a real engine')
        self.hub = ModelHub(self.home,self.engines)

    def test_automatic_config_vulkan_first_and_no_host_python(self):
        c = automatic_config(self.home,self.hub)
        self.assertEqual(c['llm']['device'], 'auto');self.assertEqual(c['image']['device'], 'auto');self.assertFalse(c['image']['enabled'])
        self.assertEqual(c['python']['mode'],'disabled');self.assertEqual(c['mcp_servers'],[])
        self.assertEqual(c['llm']['command'],[self.engines['llm']])

    def test_model_state_does_not_start_download(self):
        self.assertEqual(self.hub.status()['job']['status'],'idle')
        self.assertFalse(list(self.hub.models.iterdir()))

    def test_unknown_model_cannot_prepare(self):
        with self.assertRaises(ValueError): self.hub.prepare('../../model')

    def test_consent_cannot_be_string(self):
        with self.assertRaises(ValueError): self.hub.start('ticket','true')

    def test_missing_quote_cannot_install(self):
        with self.assertRaises(ValueError): self.hub.start('not-issued',True)

    def test_incomplete_model_cannot_activate(self):
        folder=self.hub.models/'qwen05';folder.mkdir();(folder/'model.json').write_text('{}')
        with self.assertRaises(ValueError): self.hub.activate('qwen05')

    def test_corrupt_ready_record_rejected(self):
        folder=self.hub.models/'qwen05';folder.mkdir();save_json(folder/'READY.json',{'id':'qwen05','files':[{'name':'missing','bytes':1}]})
        self.assertFalse(self.hub.installed('qwen05'))

    def test_valid_install_updates_selection_only(self):
        folder=self.hub.models/'qwen05';folder.mkdir();(folder/'model.json').write_text('{}')
        save_json(folder/'READY.json',{'id':'qwen05','files':[{'name':'model.json','bytes':2}]})
        self.hub.activate('qwen05');self.assertEqual(self.hub.active(),{'llm':'qwen05'})

    def test_download_paths_cannot_escape(self):
        for name in ('../x','/x','C:/x','a\\b','a/../x'):
            with self.subTest(name=name), self.assertRaises(ValueError): safe_name(name)

    def test_git_blob_digest(self):
        file=self.home/'test';file.write_bytes(b'abc')
        self.assertEqual(digest(file,'git'),hashlib.sha1(b'blob 3\0abc').hexdigest())

    def test_data_instance_lock(self):
        lock=InstanceLock(self.home)
        try:
            with self.assertRaises(RuntimeError): InstanceLock(self.home)
        finally: lock.close()
        other=InstanceLock(self.home);other.close()

    def test_failed_download_does_not_activate_model(self):
        quote={'id':'qwen05','revision':'a'*40,'repository':'Qwen/test','files':[{'name':'model.bin','remote':'model.bin','bytes':1,'digest':'0'*64,'algorithm':'sha256'}]}
        with patch('local_agent.model_hub.open_https',side_effect=OSError('offline')): self.hub._install(quote)
        self.assertEqual(self.hub.status()['job']['status'],'failed');self.assertFalse(self.hub.active())


@unittest.skipUnless(DocumentTools.available(),'Office libraries required for document tests')
class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ws=Workspace(self.tmp.name);self.docs=DocumentTools(self.ws)

    def test_all_four_formats_roundtrip(self):
        for fmt in ('md','docx','xlsx','pptx'):
            with self.subTest(format=fmt):
                made=self.docs.create(fmt,'# Check\n\n| Item | Value |\n| --- | --- |\n| CHECK | 42 |','Document')
                self.assertIn('CHECK',self.docs.read(made['path'])['content'])

    def test_exports_never_overwrite(self):
        a=self.docs.create('docx','first','Same');b=self.docs.create('docx','second','Same')
        self.assertNotEqual(a['path'],b['path']);self.assertIn('first',self.docs.read(a['path'])['content'])

    def test_markdown_pagination(self):
        a=self.docs.create('md','abcdefghijklmnopqrstuvwxyz')
        result=self.docs.read(a['path'],0,10);self.assertEqual(result['content'],'abcdefghij');self.assertEqual(result['next_offset'],10)
        self.assertEqual(self.docs.read(a['path'],10,10)['content'],'klmnopqrst')

    def test_excel_formula_is_literal(self):
        from openpyxl import load_workbook
        a=self.docs.create('xlsx','A,B\n=HYPERLINK("https://example.org"),42')
        book=load_workbook(self.ws.path(a['path']));self.addCleanup(book.close)
        self.assertEqual(book.active['A2'].data_type,'s');self.assertEqual(book.active['B2'].value,42)

    def test_export_rejects_unknown_format(self):
        with self.assertRaises(PolicyError): self.docs.create('exe','payload')

    def test_export_rejects_large_text(self):
        with self.assertRaises(PolicyError): self.docs.create('md','x'*200001)

    def test_read_path_cannot_escape(self):
        with self.assertRaises(PolicyError): self.docs.read('../outside.docx')

    def test_legacy_office_rejected(self):
        self.ws.write('old.doc','x')
        with self.assertRaises(PolicyError): self.docs.read('old.doc')

    def test_ooxml_dtd_rejected(self):
        p=self.ws.path('hostile.docx')
        with zipfile.ZipFile(p,'w') as archive: archive.writestr('[Content_Types].xml','<!DOCTYPE x [<!ENTITY a "bad">]>')
        with self.assertRaises(PolicyError): validate_office(p)

    def test_macro_parts_rejected(self):
        p=self.ws.path('hostile.docx')
        with zipfile.ZipFile(p,'w') as archive:
            archive.writestr('[Content_Types].xml','<types/>');archive.writestr('word/vbaProject.bin',b'x')
        with self.assertRaises(PolicyError): validate_office(p)

    def test_zip_path_traversal_rejected(self):
        p=self.ws.path('hostile.docx')
        with zipfile.ZipFile(p,'w') as archive:
            archive.writestr('[Content_Types].xml','<types/>');archive.writestr('../evil.xml','x')
        with self.assertRaises(PolicyError): validate_office(p)
