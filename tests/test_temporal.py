from backend.temporal import TemporalPoseFilter


def observation(x, marker_id=1, smoothing_ms=300, status='valid'):
    return {'config': {'temporal_smoothing_ms': smoothing_ms},
            'markers': [{'id': marker_id, 'pose_status': status,
                         'pose': {'camera_position_marker_mm': [x, 20, 30],
                                  'camera_position_world_mm': [x+1000, 20, 30],
                                  'distance_mm': 999}}]}


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
    assert filtered(spike, 'camera_position_world_mm')[0] == 1000
    assert filtered(spike, 'camera_position_world_cm')[0] == 100
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


def test_ambiguous_pose_is_never_promoted_to_filtered_position():
    smooth = TemporalPoseFilter()
    for i in range(3):
        packet = smooth.apply(observation(10, status='ambiguous'), i*.02)
    assert 'filtered' not in packet['markers'][0]['pose']


def test_approximate_world_position_is_filtered_but_keeps_its_label():
    smooth=TemporalPoseFilter()
    packet=observation(10,status='approximate')
    pose=packet['markers'][0]['pose']
    pose.pop('camera_position_world_mm')
    pose['approximate_camera_position_world_mm']=[1000.,2000.,3000.]
    result=smooth.apply(packet,0)
    filtered_pose=result['markers'][0]['pose']['filtered']
    assert filtered_pose['approximate_camera_position_world_cm']==[100.,200.,300.]
    assert 'camera_position_world_cm' not in filtered_pose
