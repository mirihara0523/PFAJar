"""Saved navigation results: no inference, correct per-slice parameters, fail closed."""
from pathlib import Path
import sys, tempfile, unittest, json, os
from unittest.mock import patch
import numpy as np
import tifffile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'py'))
from detect_live_worker import DetectionSession
from detect_live_core import windows_io_path

class SavedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.output=self.root/'out'
        self.a=self.root/'a.dotted.tif';self.b=self.root/'b.dotted.tif'
        for source in (self.a,self.b):tifffile.imwrite(source,np.arange(64*80,dtype=np.uint16).reshape(64,80))
        self.session=DetectionSession()
        self.params=dict(confidence=.5,area=0,eccentricity=0,intensity=150)
        self.records=[[2,2,12,12,.8,100,.5,100],[15,4,29,14,.9,140,.7,200]]
    def save(self,source,params=None,records=None):
        loaded=self.session.load({'source':str(source)})
        self.session.state.update(records=self.records if records is None else records,floor=.1,settings={'model':str(self.root/'model.pt'),'tile':640,'floor':.1})
        return self.session.save({'token':loaded['token'],'output':str(self.output),'params':params or self.params,'method':'custom'})
    def load(self,source=None):
        with patch('find_neurons.AutoDetectionModel.from_pretrained',side_effect=AssertionError('must not infer during navigation')):
            return self.session.load({'source':str(source or self.a),'output':str(self.output)})
    def test_independent_parameters_candidates_and_zero_reinference(self):
        self.save(self.a)
        other=dict(self.params,intensity=0,confidence=.85)
        self.save(self.b,other)
        a=self.load();self.assertEqual(a['saved_result']['params'],self.params)
        self.assertEqual(a['saved_result']['indices'],[1]);self.assertEqual(a['saved_result']['records'],self.records)
        self.assertEqual(self.session.filter({'token':a['token'],'params':dict(self.params,intensity=0)})['indices'],[0,1])
        b=self.load(self.b);self.assertEqual(b['saved_result']['params'],other)
        # A new worker/session can restore the same run after app restart.
        self.session=DetectionSession();again=self.load()
        self.assertEqual(again['saved_result']['indices'],[1])
        self.assertEqual(again['saved_result']['inference']['tile'],640)
    def test_zero_cells_is_valid_saved_result_and_load_does_not_write(self):
        self.save(self.a,records=[])
        before={str(p):p.stat().st_mtime_ns for p in self.output.rglob('*') if p.is_file()}
        result=self.load()['saved_result'];self.assertEqual(result['records'],[]);self.assertEqual(result['indices'],[])
        self.assertEqual(before,{str(p):p.stat().st_mtime_ns for p in self.output.rglob('*') if p.is_file()})
    def test_changed_image_other_branch_and_changed_prediction_rejected(self):
        saved=self.save(self.a)
        old=self.a.stat();os.utime(self.a,ns=(old.st_atime_ns,old.st_mtime_ns+1000000000))
        self.assertIsNone(self.load()['saved_result'])
        os.utime(self.a,ns=(old.st_atime_ns,old.st_mtime_ns))
        other=self.root/'other';other.mkdir();source=other/self.a.name;tifffile.imwrite(source,np.zeros((64,80),np.uint16))
        self.assertIsNone(self.load(source)['saved_result'])
        Path(saved['path']).write_bytes(b'partial pickle')
        rejected=self.load();self.assertIsNone(rejected['saved_result']);self.assertTrue(rejected['restore_warning'])
        self.assertIsNone(self.session.state['records'])
        Path(saved['path']).unlink()
        missing=self.load();self.assertIsNone(missing['saved_result']);self.assertTrue(missing['restore_warning'])
    def test_partial_save_does_not_restore_old_parameters_over_new_boxes(self):
        self.save(self.a)
        loaded=self.session.load({'source':str(self.a)})
        self.session.state.update(records=self.records,floor=.1,settings={'model':'other.pt','tile':640})
        with patch('detect_live_worker.write_json',side_effect=OSError('metadata commit failed')):
            with self.assertRaises(OSError):self.session.save({'token':loaded['token'],'output':str(self.output),'params':dict(self.params,intensity=0)})
        rejected=self.load();self.assertIsNone(rejected['saved_result']);self.assertTrue(rejected['restore_warning'])
    def test_legacy_missing_snapshot_and_missing_result_are_explicit(self):
        self.save(self.a)
        metadata=self.output/'detection_live_settings.json';raw=json.loads(metadata.read_text());raw['slices']['a.dotted'].pop('snapshot');metadata.write_text(json.dumps(raw))
        legacy=self.load();self.assertIsNone(legacy['saved_result']);self.assertIn('snapshot',legacy['restore_warning'])
        fresh=self.load(self.b);self.assertIsNone(fresh['saved_result']);self.assertIsNone(fresh['restore_warning'])
    def test_superseded_snapshot_cleaned_only_after_successful_save(self):
        self.save(self.a);self.assertEqual(len(list((self.output/'.live_saved').glob('*.json'))),1)
        self.save(self.a,dict(self.params,intensity=0))
        self.assertEqual(len(list((self.output/'.live_saved').glob('*.json'))),1)
        self.assertEqual(self.load()['saved_result']['params']['intensity'],0)

if __name__=='__main__':unittest.main()
