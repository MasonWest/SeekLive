import math
import unittest
from vision_inference import match


def vector(cosine):return [cosine,math.sqrt(1-cosine*cosine)]
def ref(identity,score,kind='cat'):return dict(entity=identity,kind=kind,vector=vector(score))


class PetReviewMatchingTests(unittest.TestCase):
    def observation(self,kind='cat'):return [dict(id='new',kind=kind,vector=[1,0],face_pixels=90)]
    def test_cross_date_pet_similarity_below_old_cutoff_is_reviewable(self):
        results=match(self.observation(),[ref('cat',.70),ref('cat',.68),ref('other',.4),ref('other',.35)])
        self.assertEqual(results[0]['entity'],'cat');self.assertLess(results[0]['score'],.8)
    def test_two_references_can_support_a_lower_score_but_not_ambiguity(self):
        self.assertEqual(len(match(self.observation(),[ref('cat',.52),ref('cat',.49),ref('other',.3)])),1)
        self.assertEqual(match(self.observation(),[ref('cat',.52),ref('cat',.49),ref('other',.51)]),[])
    def test_single_reference_and_weak_second_reference_stay_stricter(self):
        self.assertEqual(match(self.observation(),[ref('cat',.52)]),[])
        self.assertEqual(match(self.observation(),[ref('cat',.52),ref('cat',.2)]),[])
    def test_people_threshold_and_bird_manual_identity_are_unchanged(self):
        self.assertEqual(match(self.observation('person'),[ref('person',.52,'person')]),[])
        self.assertEqual(match(self.observation('bird'),[ref('bird',.99,'bird')]),[])


if __name__=='__main__':unittest.main()
