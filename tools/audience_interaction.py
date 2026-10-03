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
    'single_jersey_skew_after_washing': 'After washing, did the side seam of your T-shirt move towards the front or stay straight?',
    'discharge_print_removes_ground_dye': 'For a dark T-shirt design, would you choose ink on top or a print that lifts the dye?',
    'acid_wash_is_discharge_without_acid': 'When you compare acid-wash tees, is your doubt about the base fabric or the wash effect?',
    'pilling_set_by_materials_and_processing': 'Does your fabric specification say anything about pilling performance?',
    'mercerization_swells_cotton_for_lustre': 'Have you seen mercerized listed as a separate process on a cotton specification?',
    'polyester_oil_affinity_needs_soil_release': 'For polyester tees, has your supplier ever mentioned a soil-release finish?',
    'dtf_canva_png_3125': 'When you download your DTF design from Canva, which Size do you pick for the PNG?',
    'dtf_dpi_label_not_pixels': 'How many pixels wide is the PNG you send for a 22.8-inch DTF sheet?',
    'dtf_transparent_background': 'Does your DTF design file have a transparent background or a white one?',
    'dtf_gang_sheet_layout': 'How many designs do you usually fit on one DTF gang sheet?',
    'dtf_pieces_means_sheets': 'When you order DTF, do you count pieces as sheets or as T-shirts?',
    'dtf_press_165_180': 'Which heat press setting do you use for DTF on hoodies?',
}

FACT_QUESTIONS = {
    'jersey_face_back': QUESTIONS['single_jersey_face_back_loop_orientation'],
    'pique_tuck_structure': QUESTIONS['pique_texture_is_construction_not_fibre'],
    'discharge_printing': QUESTIONS['discharge_print_removes_ground_dye'],
    'acid_wash_discharge': QUESTIONS['acid_wash_is_discharge_without_acid'],
    'pilling_causes': QUESTIONS['pilling_set_by_materials_and_processing'],
    'mercerization_lustre': QUESTIONS['mercerization_swells_cotton_for_lustre'],
    'polyester_oleophilic': QUESTIONS['polyester_oil_affinity_needs_soil_release'],
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
    question = QUESTIONS.get(brief.get('intent_key'))
    if not question and len(fact_ids) == 1:
        question = FACT_QUESTIONS.get(fact_ids[0])
    question = question or 'Which part of this buying check would you like explained on a sample?'
    return decision.strip() + '\n\n' + question
