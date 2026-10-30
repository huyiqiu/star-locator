from star16.temporal import TemporalPoseFilter


def observation(x, marker_id=1, smoothing_ms=300, status='valid'):
    return {'config': {'temporal_smoothing_ms': smoothing_ms},
            'markers': [{'id': marker_id, 'pose_status': status,
                         'pose': {'camera_position_marker_mm': [x, 20, 30],
                                  'distance_mm': 999}}]}


def candidate(x, rms):
    return {'camera_position_marker_mm': [x, 20, 30], 'distance_mm': 999,
            'reprojection_rms_px': rms}


def ambiguous_observation(candidates, marker_id=1, smoothing_ms=300):
    return {'config': {'temporal_smoothing_ms': smoothing_ms},
            'markers': [{'id': marker_id, 'pose_status': 'ambiguous',
                         'pose': {'camera_position_marker_mm': candidates[0]['camera_position_marker_mm'],
                                  'distance_mm': 999,
                                  'candidates': candidates}}]}


def filtered(packet, name='camera_position_marker_mm'):
    return packet['markers'][0]['pose'].get('filtered', {}).get(name)


def test_single_frame_spike_does_not_move_live_position_or_mutate_raw_pnp():
    smooth = TemporalPoseFilter()
    for i in range(8):
        packet = smooth.apply(observation(0), i*.02)
    assert filtered(packet)[0] == 0
    spike = smooth.apply(observation(1000), .16)
    assert filtered(spike)[0] == 0
    assert spike['markers'][0]['pose']['camera_position_marker_mm'][0] == 1000
    assert spike['markers'][0]['pose']['filtered']['camera_position_marker_cm'][0] == 0
    assert spike['markers'][0]['pose']['filtered']['distance_mm'] < 40


def test_motion_converges_and_short_missing_frame_never_emits_stale_pose():
    smooth = TemporalPoseFilter()
    for i in range(6):
        smooth.apply(observation(0), i*.05)
    missing = smooth.apply({'config': {'temporal_smoothing_ms': 300}, 'markers': []}, .30)
    assert missing['markers'] == []
    first = smooth.apply(observation(100), .35)
    assert filtered(first)[0] < 30
    for i in range(1, 20):
        last = smooth.apply(observation(100), .35+i*.05)
    assert 90 < filtered(last)[0] < 100


def test_first_valid_frame_gap_id_change_and_disabled_filter():
    smooth = TemporalPoseFilter()
    assert filtered(smooth.apply(observation(10), 0))[0] == 10
    assert filtered(smooth.apply(observation(10), .02))[0] == 10
    assert filtered(smooth.apply(observation(10), .04))[0] == 10
    assert filtered(smooth.apply(observation(900, marker_id=2), .06))[0] == 900
    assert filtered(smooth.apply(observation(50), 1.0))[0] == 50
    off = smooth.apply(observation(60, smoothing_ms=0), 1.02)
    assert filtered(off)[0] == 60
    assert off['markers'][0]['pose']['filter_status'] == 'ready'


def test_ambiguous_pose_without_history_seeds_best_rms_and_outputs_filtered():
    smooth = TemporalPoseFilter()
    packet = smooth.apply(ambiguous_observation([candidate(100, .18), candidate(900, .27)]), 0)
    pose = packet['markers'][0]['pose']
    assert pose['branch_selection'] == 'best_rms'
    assert pose['filtered_candidate_index'] == 0
    assert filtered(packet)[0] == 100
    assert abs(pose['filtered']['distance_cm'] - 10.63014581273465) < 1e-9
    assert pose['filter_status'] == 'ready'


def test_ambiguous_pose_follows_nearest_candidate_regardless_of_rms_order():
    smooth = TemporalPoseFilter()
    smooth.apply(ambiguous_observation([candidate(100, .18), candidate(900, .27)]), 0)
    packet = smooth.apply(ambiguous_observation([candidate(900, .10), candidate(100, .30)]), .05)
    pose = packet['markers'][0]['pose']
    assert (pose['branch_selection'], pose['filtered_candidate_index']) == ('continuity', 1)
    assert filtered(packet)[0] == 100
    packet = smooth.apply(ambiguous_observation([candidate(100, .10), candidate(900, .30)]), .10)
    pose = packet['markers'][0]['pose']
    assert (pose['branch_selection'], pose['filtered_candidate_index']) == ('continuity', 0)
    assert filtered(packet)[0] == 100


def test_ambiguous_pose_reseeds_by_best_rms_after_track_expires():
    smooth = TemporalPoseFilter()
    smooth.apply(ambiguous_observation([candidate(100, .18), candidate(900, .27)]), 0)
    packet = smooth.apply(ambiguous_observation([candidate(900, .18), candidate(100, .27)]), .4)
    pose = packet['markers'][0]['pose']
    assert (pose['branch_selection'], pose['filtered_candidate_index']) == ('best_rms', 0)
    assert filtered(packet)[0] == 900


def test_ambiguous_pose_with_disabled_smoothing_outputs_chosen_candidate_only():
    smooth = TemporalPoseFilter()
    packet = smooth.apply(ambiguous_observation([candidate(100, .18), candidate(900, .27)],
                                                smoothing_ms=0), 0)
    pose = packet['markers'][0]['pose']
    assert pose['branch_selection'] == 'best_rms'
    assert filtered(packet)[0] == 100
    assert pose['filter_status'] == 'ready'
    assert not smooth.tracks


def test_ambiguous_pose_without_candidates_falls_back_to_pose():
    smooth = TemporalPoseFilter()
    packet = {'config': {'temporal_smoothing_ms': 300},
              'markers': [{'id': 1, 'pose_status': 'ambiguous',
                           'pose': {'camera_position_marker_mm': [100, 20, 30],
                                    'distance_mm': 999, 'ambiguous': True}}]}
    result = smooth.apply(packet, 0)
    pose = result['markers'][0]['pose']
    assert (pose['branch_selection'], pose['filtered_candidate_index']) == ('best_rms', 0)
    assert filtered(result)[0] == 100


def test_duplicate_ambiguous_marker_ids_are_skipped():
    smooth = TemporalPoseFilter()
    packet = ambiguous_observation([candidate(100, .18), candidate(900, .27)])
    packet['markers'].append(packet['markers'][0])
    result = smooth.apply(packet, 0)
    assert 'filtered' not in result['markers'][0]['pose']
    assert 'branch_selection' not in result['markers'][0]['pose']


def test_approximate_pose_filters_only_the_marker_relative_position():
    smooth=TemporalPoseFilter()
    result=smooth.apply(observation(10,status='approximate'),0)
    filtered_pose=result['markers'][0]['pose']['filtered']
    assert filtered_pose['camera_position_marker_cm']==[1.,2.,3.]
    assert 'camera_position_world_cm' not in filtered_pose
