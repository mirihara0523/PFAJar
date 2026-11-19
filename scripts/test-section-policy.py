"""Project policy and transactional section flip regression; temporary fixtures only."""
from pathlib import Path
import sys, tempfile, json, unittest, pickle, csv, os, subprocess, shutil
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'py'))
from section_policy import *
from section_flip import plan_flip, apply_flip, recover_flip, DetectionResult
from detect_live_core import write_json

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'.masonjar').mkdir()
        self.input=self.root/'data/counting/00_dapi'
        self.input.mkdir(parents=True)
    def test_default_conflict_hidden_and_corrupt(self):
        self.assertEqual(active_ids(['a.1','a.2'],self.input),['a.1','a.2'])
        states=save_active(self.input,{'a.1':False},{})
        self.assertEqual(active_ids(['a.1','a.2'],self.input),['a.2'])
        save_active(self.input,{'a.2':True},states)
        with self.assertRaises(ValueError): save_active(self.input,{'a.1':True},{})
        self.assertFalse(read_settings(self.input)['a.1']['active'])
        policy_path(self.input).write_text('{}')
        with self.assertRaises(ValueError): active_ids(['a.1'],self.input)
    def test_readers_explicit_and_unfiltered(self):
        from slice_input_files import list_input_files
        from slice_index import build_adjust_pairs, load_slice_list
        from apply_parcellation import discover_slice_ids
        for name in ['a.1.tif','a.2.tif']: (self.input/name).touch()
        annotations=self.root/'data/counting/01_slices/run'
        annotations.mkdir(parents=True)
        (annotations/'.masonjar').mkdir()
        for sid in ['a.1','a.2']: (annotations/('Annotation_'+sid+'.pkl')).touch()
        save_active(self.input,{'a.1':False},{})
        listing=self.root/'.masonjar/run_slice_list.json'
        write_json(listing,{'slice_ids':['a.1','a.2']})
        self.assertEqual(load_slice_list(listing),{'a.2'})
        self.assertEqual([p.name for p in list_input_files(self.input,None)],['a.2.tif'])
        self.assertEqual([p.name for p in list_input_files(self.input,str(listing))],['a.2.tif'])
        self.assertEqual([r[2] for r in build_adjust_pairs(self.input,annotations)[0]],['a.2'])
        self.assertEqual(discover_slice_ids(annotations,['a.1','a.2']),['a.2'])
        self.assertEqual(discover_slice_ids(annotations,[]),[])
    def test_shared_order_and_inactive_across_dapi_and_signal_branches(self):
        from slice_input_files import list_input_files
        from slice_index import build_adjust_pairs
        branches=[self.input,self.root/'data/counting/03_max/somata/run',self.root/'data/counting/03_max/starters/run']
        for branch in branches:
            branch.mkdir(parents=True,exist_ok=True)
            for sid in ['001','002','003']:(branch/(sid+'.tif')).touch()
        annotations=self.root/'data/counting/01_slices/run';annotations.mkdir(parents=True)
        (annotations/'.masonjar').mkdir()
        for sid in ['001','002','003']:(annotations/('Annotation_'+sid+'.pkl')).touch()
        ap_file=self.input/'alignment_session.json'
        ap_file.write_text('{"slices":[{"slice_id":"001","ap_position":40}]}')
        original_ap=ap_file.read_bytes()
        write_json(self.root/'.masonjar/slice_order.json',{'version':1,'slice_ids':['003','001','002']})
        save_active(self.input,{'001':False},{})
        for branch in branches:
            self.assertEqual([path.stem for path in list_input_files(branch,None)],['003','002'])
            self.assertEqual([row[2] for row in build_adjust_pairs(branch,annotations)[0]],['003','002'])
            self.assertEqual(active_ids(['001','002','003'],branch),['003','002'])
        self.assertEqual(ap_file.read_bytes(),original_ap)
        # New sections append after known IDs and retain the caller default if no order exists.
        self.assertEqual(active_ids(['010','002','004','003'],self.input),['003','002','004','010'])
        states=read_settings(self.input);save_active(self.input,{'001':True},states)
        self.assertEqual(active_ids(['001','002','003'],branches[1]),['003','001','002'])

    def test_old_count_totals_recomputed(self):
        from collate import _read_totals_block
        counts=self.root/'data/counting/06_quantification/count';counts.mkdir(parents=True)
        target=counts/'count_results.csv'
        with target.open('w',newline='') as stream:
            writer=csv.writer(stream)
            for sid,count in [('a.1',10),('a.2',3)]:
                writer.writerows([['Predictions_'+sid+'.pkl'],['Region Acronym','Region Name','Area (px)','Channel #0'],['VISp','visual',100,count],[]])
            writer.writerows([['Totals'],['Region Acronym','Region Name','Count','Area (px)'],['VISp','visual',13,200],[]])
        save_active(self.input,{'a.1':False},{})
        self.assertEqual(_read_totals_block(target),{'VISp':(3,100)})
    def fixture(self):
        import cv2,tifffile
        image=np.arange(60,dtype=np.uint16).reshape(6,10)
        cv2.imencode('.png',image)[1].tofile(self.input/'a.1.png')
        signal=self.root/'data/original_scans/somata';signal.mkdir(parents=True)
        with tifffile.TiffWriter(signal/'a.1.tif') as writer:
            for i in range(3): writer.write(image+i,metadata=None,photometric='minisblack')
        ann=self.root/'data/counting/01_slices/run';ann.mkdir(parents=True)
        with (ann/'Annotation_a.1.pkl').open('wb') as stream:pickle.dump(image.astype(np.uint32),stream)
        pred=self.root/'data/counting/05_predictions/somata/run';pred.mkdir(parents=True)
        record=DetectionResult();record.boxes=[[1.,1.,3.,4.]];record.scores=[.8];record.image_dimensions=(6,10)
        with (pred/'Predictions_a.1.pkl').open('wb') as stream:pickle.dump([record],stream)
        (self.input/'a.10.png').write_bytes((self.input/'a.1.png').read_bytes())
        return image,signal,ann,pred
    def test_flip_all_assets_and_roundtrip(self):
        import cv2,tifffile
        image,signal,ann,pred=self.fixture()
        other=(self.input/'a.10.png').read_bytes()
        plan=plan_flip(self.root,'a.1')
        self.assertEqual(len(plan['files']),4)
        result=apply_flip(self.root,'a.1',plan)
        self.assertTrue(Path(result['backup']).is_dir())
        self.assertTrue(read_settings(self.input)['a.1']['flip_x'])
        np.testing.assert_array_equal(cv2.imread(str(self.input/'a.1.png'),-1),image[:,::-1])
        with tifffile.TiffFile(signal/'a.1.tif') as file:
            self.assertEqual(len(file.pages),3)
            for i,page in enumerate(file.pages): np.testing.assert_array_equal(page.asarray(),(image+i)[:,::-1])
        with (ann/'Annotation_a.1.pkl').open('rb') as stream:np.testing.assert_array_equal(pickle.load(stream),image[:,::-1])
        with (pred/'Predictions_a.1.pkl').open('rb') as stream:self.assertEqual(pickle.load(stream)[0].boxes,[[6.,1.,8.,4.]])
        self.assertEqual(other,(self.input/'a.10.png').read_bytes())
        self.assertEqual(len((self.root/'.masonjar/geometry_history.jsonl').read_text().splitlines()),2)
        apply_flip(self.root,'a.1',plan_flip(self.root,'a.1'))
        self.assertFalse(read_settings(self.input)['a.1']['flip_x'])
        np.testing.assert_array_equal(cv2.imread(str(self.input/'a.1.png'),-1),image)
        with (pred/'Predictions_a.1.pkl').open('rb') as stream:self.assertEqual(pickle.load(stream)[0].boxes,[[1.,1.,3.,4.]])
    def test_flip_keeps_full_annotation_restore_and_mask_consistent(self):
        import cv2
        image,_,ann,_=self.fixture()
        full=ann/'.masonjar/annotation_full';full.mkdir(parents=True)
        with (full/'a.1.pkl').open('wb') as stream:pickle.dump(image,stream)
        masks=self.root/'.masonjar/tissue_cleanup_masks';masks.mkdir()
        mask=(image>20).astype(np.uint8)*255
        cv2.imencode('.png',mask)[1].tofile(masks/'a.1.png')
        apply_flip(self.root,'a.1',plan_flip(self.root,'a.1'))
        with (full/'a.1.pkl').open('rb') as stream:np.testing.assert_array_equal(pickle.load(stream),image[:,::-1])
        np.testing.assert_array_equal(cv2.imread(str(masks/'a.1.png'),-1),mask[:,::-1])
    def test_new_czi_extract_obeys_saved_orientation(self):
        import czi_extract as c, tifffile
        c.np,c.tiff=np,tifffile
        image=np.arange(60,dtype=np.uint16).reshape(6,10)
        write_json(policy_path(self.input),{'version':1,'sections':{'a.1':{'flip_x':True}}})
        output=self.input/'reimport.tif'
        with patch.object(c,'read_plane',lambda *args:image.copy()), patch.object(c,'_write_seam_grid_sidecar'):
            c.extract_z_stack(None,0,0,[0],output,None,.05,'a.1',self.root,cfg={'bit_depth_by_role':{c.ROLE_SIGNAL_AXONS:16}},role_key=c.ROLE_SIGNAL_AXONS)
        np.testing.assert_array_equal(tifffile.imread(output),image[:,::-1])
    def test_seam_grid_history_reset(self):
        from seam_correct import load_seam_grid
        meta=self.root/'.masonjar';grid_dir=meta/'seamgrid';grid_dir.mkdir()
        grid={'version':1,'scene_extent_px':[100,100],'vertical':{'boundaries_frac':[.2,.5]},'horizontal':{'boundaries_frac':[.3]}}
        # Use the actual sidecar contract, retaining original geometry.
        grid['slice_id']='a.1'
        (grid_dir/'a.1.json').write_text(json.dumps(grid))
        image=self.input/'a.1.png'
        relative=image.relative_to(self.root).as_posix()
        (meta/'geometry_history.jsonl').write_text(''.join(json.dumps({'kind':'file','ok':True,'file':relative,**event})+'\n' for event in [{'ops':['flipX']},{'reset':True,'ops':[]}]))
        actual=load_seam_grid(meta,'a.1',image)
        self.assertIsNotNone(actual)
        self.assertEqual(actual['vertical']['boundaries_frac'],[.2,.5])

    def test_atlas_template_mirror_is_cache_sensitive(self):
        import ast,types
        tree=ast.parse((ROOT/'py/map.py').read_text(encoding='utf-8'))
        cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='AtlasSlice')
        method=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name=='set_slice')
        module=ast.Module(body=[method],type_ignores=[])
        namespace={'np':np,'slice_3d_volume':lambda volume,*args:volume,'crop_planar_for_hemisphere':lambda image,hemi:image}
        exec(compile(module,'map-set-slice','exec'),namespace)
        obj=types.SimpleNamespace(ap_position=1,x_angle=0,y_angle=0,hemisphere='W',section_flip_x=False)
        image=np.arange(30).reshape(5,6)
        namespace['set_slice'](obj,image,image)
        np.testing.assert_array_equal(obj.image,image)
        obj.section_flip_x=True
        namespace['set_slice'](obj,image,image)
        np.testing.assert_array_equal(obj.label,image[:,::-1])

    def javascript_roundtrip(self, payload):
        node = os.environ.get('PFA_TEST_NODE') or shutil.which('node')
        if not node:
            candidate = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
            node = str(candidate) if candidate.is_file() else None
        if not node:
            self.skipTest('Node.js is required for the Electron JSON transport regression')
        result = subprocess.run([node, '-e', 'let s="";process.stdin.on("data",c=>s+=c);process.stdin.on("end",()=>process.stdout.write(JSON.stringify(JSON.parse(s))));'],
                                input=json.dumps(payload), text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def test_flip_review_survives_actual_javascript_json_roundtrip(self):
        self.fixture()
        image=self.input/'a.1.png'
        # Realistic Windows timestamp which loses 100 ns when sent as a JS Number.
        stamp=1790369147439185600
        os.utime(image,ns=(image.stat().st_atime_ns,stamp))
        actual=image.stat().st_mtime_ns
        self.assertNotEqual(self.javascript_roundtrip({'mtime_ns':actual})['mtime_ns'],actual)
        plan=plan_flip(self.root,'a.1')
        self.assertTrue(all(isinstance(row['mtime_ns'],str) for row in plan['files']))
        received=self.javascript_roundtrip(plan)
        self.assertEqual(received,plan)
        result=apply_flip(self.root,'a.1',received)
        self.assertEqual(result['files'],4)
        self.assertTrue(read_settings(self.input)['a.1']['flip_x'])

    def test_json_roundtrip_still_rejects_real_timestamp_and_settings_changes(self):
        self.fixture();image=self.input/'a.1.png'
        plan=self.javascript_roundtrip(plan_flip(self.root,'a.1'))
        before=image.read_bytes();stamp=image.stat()
        os.utime(image,ns=(stamp.st_atime_ns,stamp.st_mtime_ns+100))
        self.assertNotEqual(image.stat().st_mtime_ns,stamp.st_mtime_ns)
        with self.assertRaisesRegex(ValueError,'changed after review'):
            apply_flip(self.root,'a.1',plan)
        self.assertEqual(image.read_bytes(),before)
        self.assertFalse((self.root/'.masonjar/section_flip_backups').exists())
        plan=self.javascript_roundtrip(plan_flip(self.root,'a.1'))
        save_active(self.input,{'a.1':False},{})
        with self.assertRaisesRegex(ValueError,'changed after review'):
            apply_flip(self.root,'a.1',plan)
        self.assertEqual(image.read_bytes(),before)
        self.assertFalse((self.root/'.masonjar/section_flip_backups').exists())

    def test_refuse_stale_or_unknown_without_changes(self):
        self.fixture();plan=plan_flip(self.root,'a.1');p=self.input/'a.1.png';old=p.read_bytes()
        os.utime(p,ns=(p.stat().st_atime_ns,p.stat().st_mtime_ns+1000000000))
        with self.assertRaises(ValueError): apply_flip(self.root,'a.1',plan)
        self.assertEqual(p.read_bytes(),old)
        unknown=self.root/'data/counting/01_slices/run/a.1.bin';unknown.touch()
        with self.assertRaises(ValueError):plan_flip(self.root,'a.1')
    def test_commit_failure_rollback_and_pending_recovery(self):
        self.fixture();plan=plan_flip(self.root,'a.1')
        snapshots={r['path']:(self.root/r['path']).read_bytes() for r in plan['files']}
        original_replace=os.replace;commits=[0]
        def fail_once(source,target):
            if '/staged/' in str(source).replace('\\','/'):
                commits[0]+=1
                if commits[0]==2: raise OSError('injected commit failure')
            return original_replace(source,target)
        with patch('section_flip.os.replace',side_effect=fail_once):
            with self.assertRaises(OSError): apply_flip(self.root,'a.1',plan)
        for name,data in snapshots.items():self.assertEqual((self.root/name).read_bytes(),data)
        self.assertFalse(read_settings(self.root))
        backups=list((self.root/'.masonjar/section_flip_backups').iterdir());journal=json.loads((backups[0]/'journal.json').read_text())
        write_json(self.root/'.masonjar/section_flip_pending.json',journal)
        with self.assertRaises(ValueError):active_ids(['a.1'],self.input)
        (self.input/'a.1.png').write_bytes(b'partial replacement')
        self.assertTrue(recover_flip(self.root))
        self.assertEqual((self.input/'a.1.png').read_bytes(),snapshots['data/counting/00_dapi/a.1.png'])

if __name__=='__main__':unittest.main()
