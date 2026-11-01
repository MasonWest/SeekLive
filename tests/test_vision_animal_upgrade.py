import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from vision_inference import Engine
from vision_pipeline import digest
from vision_image import np,combine_pet_detections


class AnimalUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='tmp/vision-lab');self.base=Path(self.temp.name);self.photos=self.base/'photos';self.photos.mkdir();self.path=self.photos/'image.jpg';Image.new('RGB',(160,120),(30,90,100)).save(self.path)
        st=self.path.stat();self.source=dict(id='image',root=str(self.photos),rel='image.jpg',size=st.st_size,mtime_ns=st.st_mtime_ns,_animal_upgrade=True,_previous_people=[dict(id='face',kind='person',box=[.1,.1,.2,.2],vector=[1.,0.],captured='2025:01:01 12:00:00')],_previous_animals=[],_upgrade_captured='2025:01:01 12:00:00')
        self.engine=Engine.__new__(Engine);self.engine.assets=self.base/'assets';self.engine.assets.mkdir();self.engine.asset_bytes=0;self.engine.animals=object();self.engine.animals_recall=object();self.engine.pet=object()
        target=self.engine.assets/'image'/digest([st.st_size,st.st_mtime_ns])[:12];target.mkdir(parents=True);Image.new('RGB',(80,60),(30,90,100)).save(target/'preview.jpg')
    def tearDown(self):self.temp.cleanup()
    def test_upgrade_without_animals_reuses_preview_and_person_vectors(self):
        with patch('vision_image.detect_pets',return_value=[]),patch('vision_image.read_image') as read:
            result=self.engine.analyse(self.source,[],lambda:None,[])
        self.assertEqual(result,self.source['_previous_people']);read.assert_not_called()
    def test_baseline_cat_survives_stronger_detector_miss(self):
        cat=dict(id='old-cat',kind='cat',box=[.1,.1,.9,.9],score=.7,vector=[1.,0.],source_version=[self.source['size'],self.source['mtime_ns']])
        self.source['_previous_animals']=[cat]
        with patch('vision_image.detect_pets',return_value=[]),patch('vision_image.pet_embedding',return_value=np.array([1.,0.])):
            result=self.engine.analyse(self.source,[],lambda:None,[cat])
        self.assertTrue(any(r['id']=='old-cat' for r in result));self.assertEqual(result[0],self.source['_previous_people'][0])
    def test_overlapping_detection_of_another_species_keeps_separate_identity(self):
        bird=dict(id='known-bird',kind='bird',box=[.1,.1,.9,.9],vector=None)
        with patch('vision_image.detect_pets',return_value=[(np.array([8.,6.,72.,54.]),.8,'cat')]),patch('vision_image.pet_embedding',return_value=np.array([1.,0.])):
            result=self.engine.analyse(self.source,[],lambda:None,[bird])
        cats=[o for o in result if o['kind']=='cat']
        self.assertEqual(len(cats),1);self.assertNotEqual(cats[0]['id'],'known-bird')
    def test_combination_keeps_new_boxes_and_baseline_misses_without_duplicates(self):
        box=np.array([10.,10.,100.,100.]);other=np.array([130.,130.,200.,200.]);near=box+1
        result=combine_pet_detections([(box,.8,'cat')],[(near,.7,'cat'),(other,.6,'cat')])
        self.assertEqual(len(result),2);self.assertTrue(np.array_equal(result[0][0],box))


if __name__=='__main__':unittest.main()
