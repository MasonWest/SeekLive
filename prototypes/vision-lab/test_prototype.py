import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from run import conservative_groups,detect_pets,inventory,isolated_output,pet_embedding
from review import build_review
from evaluate import evaluate,dataset_id
from discover import profiles,rank_face,select_sources,cache_valid
from aggregate_discoveries import curate,aggregate


class PrototypeTests(unittest.TestCase):
    def test_detector_includes_birds_as_a_separate_species(self):
        class Net:
            def get_inputs(self):return [type('Input',(),{'name':'input'})()]
            def run(self,*_):
                out=np.zeros((1,3549,85),np.float32)
                out[0,0,:4]=[6,6,2,2];out[0,0,4]=.99;out[0,0,5+14]=.95
                return [out]
        detections=detect_pets(Net(),np.zeros((416,416,3),np.uint8))
        self.assertEqual(len(detections),1)
        self.assertEqual(detections[0][2],'bird')

    def test_curating_keeps_new_batch_visible_and_collapses_duplicates(self):
        records=[dict(id='old',source_run='old',score=.99,dhash='0'),dict(id='new',source_run='new',score=.7,dhash='ffff'),dict(id='duplicate',source_run='new',score=.69,dhash='fffe')]
        chosen=curate(records)
        self.assertEqual([x['id'] for x in chosen],['new','old'])

    def test_aggregate_refuses_overlapping_or_incompatible_batches(self):
        with tempfile.TemporaryDirectory(dir='tmp/vision-lab') as folder:
            root=Path(folder);base=root/'base';base.mkdir()
            result=dict(samples=[],faces=[],pets=[],face_groups=[],summary={},comparisons=[])
            (base/'results.private.json').write_text(json.dumps(result),encoding='utf-8')
            folders=[]
            for name in ('a','b'):
                run=root/name;run.mkdir();folders.append(run)
                data=dict(base_dataset_id=dataset_id(result),reference_signature='refs',models=[],candidates=[],people=[])
                (run/'discovery.private.json').write_text(json.dumps(data),encoding='utf-8')
                (run/'samples.private.json').write_text(json.dumps([dict(id='same')]),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Overlapping'):aggregate(folders,root/'out',base)
            data['reference_signature']='changed'
            (folders[1]/'discovery.private.json').write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'differ'):aggregate(folders,root/'out',base)

    def test_discovery_cache_invalidates_model_source_or_broken_vectors(self):
        with tempfile.TemporaryDirectory(dir='tmp/vision-lab') as folder:
            out=Path(folder);(out/'preview').write_bytes(b'fixture');(out/'crop').write_bytes(b'fixture')
            cached=dict(version=[123,456],signature='model',preview='preview',faces=[dict(crop='crop',vector=[0.]*128)])
            self.assertTrue(cache_valid(cached,[123,456],'model',out))
            self.assertFalse(cache_valid(cached,[124,456],'model',out))
            self.assertFalse(cache_valid(cached,[123,456],'new-model',out))
            cached['faces'][0]['vector']=[float('nan')]*128
            self.assertFalse(cache_valid(cached,[123,456],'model',out))
            cached['faces'][0]['vector']=[0.]*64
            self.assertFalse(cache_valid(cached,[123,456],'model',out))

    def test_discovery_only_uses_confirmed_followed_people(self):
        result=dict(samples=[],models=[],face_groups=[dict(id='p',members=['f']),dict(id='q',members=['g']),dict(id='r',members=['h'])])
        labels=dict(dataset_id=dataset_id(result),group_names={'p':'A','q':'B','r':'C'},face_checks={'f':'keep','g':'keep','h':'unsure'},person_interest={'q':'unfollowed'})
        self.assertEqual(profiles(result,labels),[dict(id='p',name='A',references=['f'])])

    def test_confirmed_discoveries_extend_references_and_respect_exclusions(self):
        result=dict(samples=[],models=[],face_groups=[dict(id='p',members=['f'])])
        labels=dict(dataset_id=dataset_id(result),group_names={'p':'A'},face_checks={'f':'keep'})
        additions=[dict(id='new',person='p')]
        self.assertEqual(profiles(result,labels,additions)[0]['references'],['f','new'])
        labels['face_checks']['new']='reject'
        self.assertEqual(profiles(result,labels,additions)[0]['references'],['f'])
        labels['person_interest']={'p':'unfollowed'}
        self.assertEqual(profiles(result,labels,additions),[])

    def test_discovery_abstains_on_ambiguous_identity(self):
        people=[dict(id='a'),dict(id='b')]
        refs={'a':[np.array([1.,0.])],'b':[np.array([.99,.1])]}
        self.assertIsNone(rank_face(np.array([1.,0.]),people,refs))
        refs['b']=[np.array([0.,1.])]
        candidate=rank_face(np.array([1.,0.]),people,refs)
        self.assertEqual(candidate['person'],'a')
        self.assertEqual(candidate['status'],'unverified')
        self.assertIsNone(rank_face(np.array([.5,.5]),people,refs))

    def test_discovery_requires_support_from_second_reference(self):
        self.assertIsNone(rank_face(np.array([1.,0.]),[dict(id='a')],{'a':[np.array([1.,0.]),np.array([0.,1.])]}))

    def test_discovery_excludes_previously_scanned_and_spreads_directories(self):
        sources=[dict(id=str(i),namespace='lib',rel=f'{i%2}/photo{i}') for i in range(20)]
        selected=select_sources(sources,{'0','1'},6)
        self.assertEqual(len(selected),6)
        self.assertFalse({'0','1'}&{x['id'] for x in selected})
        self.assertEqual(len({x['rel'].split('/')[0] for x in selected}),2)

    def test_private_seed_preserves_labels_and_validates_dataset(self):
        with tempfile.TemporaryDirectory(dir='tmp/vision-lab',prefix='unit-') as folder:
            out=Path(folder)
            result=dict(samples=[],faces=[],pets=[],face_groups=[],summary={},comparisons=[])
            seed=dict(dataset_id=dataset_id(result),group_names={'P001':'Synthetic Name'},face_checks={},person_links={})
            (out/'labels.seed.private.json').write_text(json.dumps(seed),encoding='utf-8')
            build_review(out,result)
            page=(out/'review.html').read_text(encoding='utf-8')
            payload=page.split('<script id="dataset" type="application/json">',1)[1].split('</script>',1)[0]
            self.assertEqual(json.loads(payload)['initial_labels'],seed)
            seed['dataset_id']='wrong'
            (out/'labels.seed.private.json').write_text(json.dumps(seed),encoding='utf-8')
            with self.assertRaises(ValueError):build_review(out,result)

    def test_evaluation_does_not_treat_unreviewed_as_correct(self):
        result=dict(samples=[],models=[],pets=[dict(id='a',kind='cat'),dict(id='b',kind='dog')],comparisons=[])
        labels=dict(dataset_id=dataset_id(result),animal_checks={'a':'cat','b':'unsure'})
        report=evaluate(result,labels)
        self.assertEqual(report['detection_review']['reviewed'],1)
        self.assertEqual(report['detection_review']['total_candidates'],2)
        with self.assertRaises(ValueError):evaluate(result,dict(dataset_id='wrong'))

    def test_no_transitive_identity_merge(self):
        vectors=np.array([[1,0],[np.cos(np.pi/6),np.sin(np.pi/6)],[.5,np.sqrt(.75)]],np.float32)
        records=[dict(sample=str(i),kind='person') for i in range(3)]
        groups=conservative_groups(records,vectors,.8)
        self.assertEqual(sorted(map(len,groups)),[1,2])
        self.assertFalse(any(0 in g and 2 in g for g in groups))

    def test_same_photo_and_species_stay_separate(self):
        records=[dict(sample='a',kind='cat'),dict(sample='a',kind='cat'),dict(sample='b',kind='dog')]
        self.assertEqual(len(conservative_groups(records,np.ones((3,1)),.8)),3)

    def test_detector_uses_top_class_not_just_a_cat_score(self):
        class Net:
            def get_inputs(self):return [type('Input',(),{'name':'input'})()]
            def run(self,*_):
                out=np.zeros((1,3549,85),np.float32)
                out[0,0,:4]=[6,6,2,2];out[0,0,4]=.99
                out[0,0,5]=.99 # Person is more likely than cat.
                out[0,0,5+15]=.8
                return [out]
        self.assertEqual(detect_pets(Net(),np.zeros((416,416,3),np.uint8)),[])

    def test_pet_normalization_is_rgb_and_model_contract(self):
        class Net:
            tensor=None
            def get_inputs(self):return [type('Input',(),{'name':'input'})()]
            def run(self,_,data):self.tensor=data['input'];return [np.array([[3,4]],np.float32)]
        net=Net();v=pet_embedding(net,Image.new('RGB',(100,200),(255,0,0)))
        self.assertEqual(net.tensor.shape,(1,3,224,224))
        self.assertAlmostEqual(float(net.tensor[0,0,0,0]),(1-.485)/.229,places=4)
        self.assertAlmostEqual(float(net.tensor[0,2,0,0]),-.406/.225,places=4)
        np.testing.assert_allclose(v,[.6,.8],atol=1e-6)

    def test_source_output_guard(self):
        with self.assertRaises(ValueError):isolated_output(Path('tmp/photos/result'),[Path('tmp/photos')])
        isolated_output(Path('tmp/results'),[Path('tmp/photos')])

    def test_duplicate_library_ids_do_not_mix_samples(self):
        with tempfile.TemporaryDirectory(dir='tmp/vision-lab',prefix='unit-') as folder:
            base=Path(folder)
            for name in ('a','b'):
                (base/name).mkdir();Image.new('RGB',(30,30)).save(base/name/'same.jpg')
            samples,stats=inventory({'libraries':[{'id':'duplicate','root':str(base/'a')},{'id':'duplicate','root':str(base/'b')}]},2)
            self.assertEqual(stats['total_images'],2)
            self.assertEqual(len({s['id'] for s in samples}),2)

    def test_review_is_self_contained_and_escapes_script_data(self):
        with tempfile.TemporaryDirectory(dir='tmp/vision-lab',prefix='unit-') as folder:
            out=Path(folder);Image.new('RGB',(20,20)).save(out/'preview.jpg')
            data=dict(summary={},samples=[dict(id='test',preview='preview.jpg',captured='</script><script>bad()</script>')],faces=[],pets=[],face_groups=[],comparisons=[])
            build_review(out,data)
            page=(out/'review.html').read_text(encoding='utf-8')
            self.assertIn("connect-src 'none'",page)
            self.assertNotIn('</script><script>bad()',page)
            self.assertIn('data:image/jpeg;base64,',page)
            self.assertIn('if(liveDatabase)',page)
            payload=page.split('<script id="dataset" type="application/json">',1)[1].split('</script>',1)[0]
            self.assertEqual(json.loads(payload)['samples'][0]['captured'],data['samples'][0]['captured'])


if __name__=='__main__':unittest.main()
