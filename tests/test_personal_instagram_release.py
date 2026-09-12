import copy
import base64
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, Mock

from tools import personal_instagram_release as r
from tests.test_reviewed_ai_reels import FakeBackend

NOW = datetime.fromisoformat('2026-11-01T17:00:00+05:30')
WATCH = 'https://www.youtube.com/watch?v=ABCDEFGHIJK'
HTML = ('<a id="full-story-video" href="'+WATCH+'">Watch full story</a><a href="https://sale91.com">Store</a>').encode()
JOB = {'id':'personal-20260912-ig-v2', 'status':'reviewed','account_id':r.ACCOUNT_ID,'publish_at':'2026-11-01T17:00:00+05:30','release_by':'2026-11-01T20:00:00+05:30','caption':'My personal experience. Full video in my profile.','video_url':'https://www.bulkplaintshirt.com/p/source.mp4','video_sha256':'a'*64,'cover_url':'https://www.bulkplaintshirt.com/p/cover.jpg','cover_sha256':'b'*64,'user_selection':{'id':'PERSONAL-IG','version':'2.0','sha256':'a'*64,'cover_sha256':'b'*64,'approved_at':'2026-09-12T15:00:00+05:30','public_release_approved':True,'real_recorded_speech':True},'dependency':{'youtube_id':'ABCDEFGHIJK','youtube_channel_id':r.MAIN_CHANNEL,'youtube_public_after':'2026-10-31T19:00:00+05:30','profile_url':'https://www.bulkplaintshirt.com/p/my-story.html','primary_store_url':'http://sale91.com','landing_sha256':hashlib.sha256(HTML).hexdigest(),'native_profile_evidence':{'account_id':r.ACCOUNT_ID,'username':r.ACCOUNT_USERNAME,'profile_url':'https://www.bulkplaintshirt.com/p/my-story.html','primary_store_url':'http://sale91.com','secondary_link_verified':True,'verified_at':'2026-09-12T15:00:00+05:30','screenshot_sha256':'c'*64}}}
VIDEO = {'items':[{'id':'ABCDEFGHIJK','snippet':{'channelId':r.MAIN_CHANNEL},'status':{'privacyStatus':'public','uploadStatus':'processed','embeddable':True},'contentDetails':{}}]}
EMBED = {'provider_name':'YouTube','html':'<iframe src="https://www.youtube.com/embed/ABCDEFGHIJK?feature=oembed"></iframe>'}

class API:
    def __init__(self): self.posts=[]; self.fail=None; self.bad_caption=False; self.website='http://sale91.com'
    def identity(self): return {'id':r.ACCOUNT_ID,'username':r.ACCOUNT_USERNAME}
    def profile(self): return dict(self.identity(),website=self.website)
    def create(self,body):
        self.posts.append(('create',copy.deepcopy(body)))
        if self.fail=='create': raise r.Error('unknown create')
        return '123'
    def status(self,ident): return 'FINISHED'
    def publish(self,ident):
        self.posts.append(('publish',ident))
        if self.fail=='publish': raise r.Error('unknown publish')
        return '456'
    def media(self,ident): return dict(self.identity(),id=ident,owner={'id':r.ACCOUNT_ID},caption='wrong' if self.bad_caption else JOB['caption'])

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name); self.api=API(); self.backend=FakeBackend(); self.store=r.StateStore(self.path/'a.json',self.backend)
    def tearDown(self): self.tmp.cleanup()
    def run_job(self,**kw):
        args=dict(api=self.api,job=copy.deepcopy(JOB),store=self.store,execute=True,clock=lambda:NOW,assets_check=lambda j:None,dependencies=lambda *a:{'checked':True})
        args.update(kw);return r.run_job(**args)
    def test_load_exact_real_recording(self): self.assertEqual(r.validate_job(copy.deepcopy(JOB)),JOB)
    def test_changed_selection_hash_and_cover_rejected(self):
        for key in ['sha256','cover_sha256','real_recorded_speech','public_release_approved']:
            j=copy.deepcopy(JOB);j['user_selection'][key]='wrong'
            with self.subTest(key=key),self.assertRaises(r.Error):r.validate_job(j)
    def test_ai_and_unknown_fields_not_accepted(self):
        j=copy.deepcopy(JOB);j['is_ai_generated']=True
        with self.assertRaises(r.Error):r.validate_job(j)
    def test_no_native_evidence_no_release(self):
        j=copy.deepcopy(JOB);j['dependency']['native_profile_evidence']['secondary_link_verified']=False
        with self.assertRaises(r.Error):self.run_job(job=j)
        self.assertEqual(self.api.posts,[])
    def test_wrong_native_account_or_link_rejected(self):
        for k,v in [('account_id','123'),('profile_url','https://evil.invalid/'),('primary_store_url','https://elsewhere.invalid/')]:
            j=copy.deepcopy(JOB);j['dependency']['native_profile_evidence'][k]=v
            with self.subTest(k=k),self.assertRaises(r.Error):r.validate_job(j)
    def test_hold_before_or_after_window(self):
        for day in ['2026-11-01T16:59:59+05:30','2026-11-01T20:00:01+05:30']:
            with self.assertRaises(r.Error):self.run_job(clock=lambda:datetime.fromisoformat(day))
        self.assertEqual(self.api.posts,[])
    def test_expiry_not_unbounded(self):
        j=copy.deepcopy(JOB);j['release_by']='2026-11-02T17:00:00+05:30'
        with self.assertRaises(r.Error):r.validate_job(j)
    def test_hash_error_before_mutation(self):
        with self.assertRaises(r.Error):self.run_job(assets_check=Mock(side_effect=r.Error('changed bytes')))
        self.assertEqual(self.api.posts,[]);self.assertEqual(self.backend.records,{})
    def test_dependency_error_before_mutation(self):
        with self.assertRaises(r.Error):self.run_job(dependencies=Mock(side_effect=r.Error('private full story')))
        self.assertEqual(self.api.posts,[]);self.assertEqual(self.backend.records,{})
    def test_dependency_rechecked_before_publish(self):
        dep=Mock(side_effect=[{'checked':True},r.Error('link disappeared')])
        with self.assertRaises(r.Error):self.run_job(dependencies=dep)
        self.assertEqual([p[0] for p in self.api.posts],['create'])
        self.assertEqual(self.store.read()['parent_id'],'123')
    def test_preflight_readonly(self):
        self.run_job(execute=False)
        self.assertEqual(self.api.posts,[]);self.assertEqual(self.backend.records,{})
    def test_future_preflight_reports_hold_without_claiming_release_ready(self):
        result=self.run_job(execute=False,dependencies=Mock(side_effect=r.Error('Full story not yet public')))
        self.assertTrue(result['dry_run']);self.assertFalse(result['ready_for_release'])
        self.assertIn('not yet public',result['dependency_hold']);self.assertEqual(self.api.posts,[])
    def test_success_and_rerun_reuses_media(self):
        self.assertEqual(self.run_job()['phase'],'published_verified')
        self.run_job(dependencies=Mock(side_effect=r.Error('later changed')))
        self.assertEqual([p[0] for p in self.api.posts],['create','publish'])
        self.assertNotIn('is_ai_generated',self.api.posts[0][1])
    def test_create_and_publish_ambiguity_never_retried(self):
        for stage in ['create','publish']:
            self.backend=FakeBackend();self.store=r.StateStore(self.path/(stage+'.json'),self.backend);self.api=API();self.api.fail=stage
            with self.assertRaises(r.Error):self.run_job()
            count=len(self.api.posts)
            with self.assertRaises(r.Error):self.run_job()
            self.assertEqual(len(self.api.posts),count)
    def test_remote_state_required(self):
        with self.assertRaises(r.Error):self.run_job(store=r.StateStore(self.path/'local.json'))
        self.assertEqual(self.api.posts,[])
    def test_conditional_state_failure_no_post(self):
        self.backend.fail_next=True
        with self.assertRaises(r.Error):self.run_job()
        self.assertEqual(self.api.posts,[])
    def test_changed_saved_copy_rejected(self):
        self.run_job();j=copy.deepcopy(JOB);j['caption']+='!'
        with self.assertRaises(r.Error):self.run_job(job=j)
    def test_failed_readback_keeps_id_no_republish(self):
        self.api.bad_caption=True
        with self.assertRaises(r.Error):self.run_job()
        with self.assertRaises(r.Error):self.run_job()
        self.assertEqual(len(self.api.posts),2);self.assertEqual(self.store.read()['media_id'],'456')
    def dep(self,video=None,html=HTML,embed=None):
        response=Mock(status_code=200,content=html)
        with patch.dict(os.environ,{'YOUTUBE_API_KEY_1':'test'}),patch.object(r,'public_json',side_effect=[video or VIDEO,embed or EMBED]),patch.object(r.requests,'get',return_value=response):
            return r.check_dependencies(self.api,JOB,clock=lambda:NOW)
    def test_public_video_and_exact_landing_pass(self):self.assertTrue(self.dep()['primary_store_verified'])
    def test_private_unlisted_processing_wrong_owner_block(self):
        for section,key,value in [('status','privacyStatus','private'),('status','privacyStatus','unlisted'),('status','uploadStatus','uploaded'),('status','embeddable',False),('snippet','channelId','other')]:
            v=copy.deepcopy(VIDEO);v['items'][0][section][key]=value
            with self.subTest(value=value),self.assertRaises(r.Error):self.dep(video=v)
    def test_india_block_and_age_restriction_hold(self):
        for details in [{'regionRestriction':{'blocked':['IN']}},{'regionRestriction':{'allowed':['US']}},{'regionRestriction':{'allowed':[]}},{'contentRating':{'ytRating':'ytAgeRestricted'}}]:
            v=copy.deepcopy(VIDEO);v['items'][0]['contentDetails']=details
            with self.assertRaises(r.Error):self.dep(video=v)
    def test_profile_store_change_holds(self):
        self.api.website='https://changed.invalid'
        with self.assertRaises(r.Error):self.dep()
    def test_changed_landing_holds(self):
        with self.assertRaises(r.Error):self.dep(html=HTML+b' changed')
    def test_wrong_oembed_holds(self):
        with self.assertRaises(r.Error):self.dep(embed={'provider_name':'YouTube','html':'<iframe src="https://youtube.com/embed/WRONGVIDEO1"></iframe>'})
    def test_encrypted_job_authentication_and_binding(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key=os.urandom(32);nonce=os.urandom(12)
        env={'id':JOB['id'],'status':'active','publish_at':JOB['publish_at'],'job_sha256':r.digest(JOB),'nonce':base64.b64encode(nonce).decode(),'ciphertext':base64.b64encode(AESGCM(key).encrypt(nonce,json.dumps(JOB).encode(),JOB['id'].encode())).decode()}
        path=self.path/'job.json';path.write_text(json.dumps(env))
        with patch.dict(os.environ,{'PERSONAL_INSTAGRAM_RELEASE_KEY':key.hex()}):
            self.assertEqual(r.read_encrypted(path),JOB)
            env['job_sha256']='0'*64;path.write_text(json.dumps(env))
            with self.assertRaises(r.Error):r.read_encrypted(path)
    def test_s3_absent_uses_exact_prefix_no_get403(self):
        c=Mock();c.list_objects_v2.return_value={'Contents':[]};b=r.S3Backend(c)
        self.assertEqual(b.read('a.json'),(None,None));c.get_object.assert_not_called()
        self.assertEqual(c.list_objects_v2.call_args.kwargs['Prefix'],r.PREFIX+'a.json')
    def test_s3_existing_get_denied_blocks(self):
        c=Mock();c.list_objects_v2.return_value={'Contents':[{'Key':r.PREFIX+'a.json'}]};c.get_object.side_effect=Exception('403')
        with self.assertRaises(r.Error):r.S3Backend(c).read('a.json')
    def test_s3_save_rejects_plaintext_caption(self):
        with self.assertRaises(r.Error):r.S3Backend(Mock()).save('a.json',{'format':'personal-reel-v1','caption':'sensitive'},None)

if __name__=='__main__':unittest.main()
