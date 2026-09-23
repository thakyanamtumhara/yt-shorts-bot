QUESTIONS = {
    'knit_stretch_not_fibre_identity': 'When buying a stretchy tee, is your doubt about the fibre or the fit?',
    'french_terry_unbrushed_vs_napped': 'For your next garment, are you choosing a looped or brushed inside surface?',
    'english_cotton_count_direction': 'When comparing yarn numbers, is the count system stated on your specification?',
    'rib_accordion_structure': 'When checking a collar, is your doubt about stretch or its shape after washing?',
    'biopolish_vs_compaction_distinct': 'On a fabric sheet, which is less clear to you: surface finish or shrinkage control?',
    'combing_short_fibre_removal_before_spinning': 'Which term on your cotton specification needs explaining: combing or spinning?',
    'single_jersey_face_back_loop_orientation': 'Which side of the T-shirt fabric are you comparing: the outside or the inside?',
    'pique_texture_is_construction_not_fibre': 'When choosing a polo, are you comparing its texture or its fibre blend?',
    'fabric_area_mass_vs_whole_garment_mass': 'Does your specification show fabric GSM or the weight of the complete T-shirt?',
    'knit_hem_coverseam_vs_joining_overedge': 'Which seam would you like explained on a sample: the hem or a joining seam?',
}


def interaction_copy(topic):
    brief = getattr(topic, 'brief', None)
    if not isinstance(brief, dict):
        return ''
    decision = brief.get('buyer_decision')
    fact_ids = brief.get('fact_ids')
    evidence = brief.get('evidence')
    if (not isinstance(decision, str) or not decision.strip()
            or not isinstance(fact_ids, list) or not fact_ids
            or not isinstance(evidence, dict)
            or any(not isinstance(evidence.get(key), dict) or not evidence[key].get('source_url') for key in fact_ids)):
        return ''
    question = QUESTIONS.get(brief.get('intent_key'),
                             'Which part of this buying check would you like explained on a sample?')
    return decision.strip() + '\n\n' + question
