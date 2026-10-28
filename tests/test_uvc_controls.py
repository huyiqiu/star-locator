from types import SimpleNamespace

import pytest

from backend import uvc_controls as module


def fake_uvc(monkeypatch, tmp_path):
    binary = tmp_path / 'uvc-util'
    binary.write_text('test executable')
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    values = {'auto-exposure-mode': 8, 'exposure-time-abs': 157, 'gain': 0}
    defaults = values.copy()
    bounds = {'exposure-time-abs': (1, 5000), 'gain': (0, 100)}
    writes = []

    def run(command, **kwargs):
        args = command[1:]
        if args == ['-d']:
            out = '0  0x05a3:0x9230  0x00110000  1.00  USB 2.0 Camera\n'
        elif args[-1] == '-c':
            out = 'UVC controls implemented by this device:\n  auto-exposure-mode\n  exposure-time-abs\n  gain\n'
        elif '-S' in args:
            name = args[args.index('-S') + 1]
            if name == 'auto-exposure-mode':
                out = f'{name} {{\n  step-size: 9\n  default-value: 8\n  current-value: {values[name]}\n}}\n{name} = {values[name]}\n'
            else:
                minimum, maximum = bounds[name]
                out = (f'{name} {{\n  minimum: {minimum}\n  maximum: {maximum}\n  step-size: 1\n'
                       f'  default-value: {defaults[name]}\n  current-value: {values[name]}\n}}\n{name} = {values[name]}\n')
        elif '-s' in args:
            name, raw = args[args.index('-s') + 1].split('=')
            value = int(raw)
            writes.append((name, value))
            if name == 'auto-exposure-mode' or values['auto-exposure-mode'] == 1:
                values[name] = value
            out = ''
        else:
            raise AssertionError(command)
        return SimpleNamespace(returncode=0, stdout=out, stderr='')

    monkeypatch.setattr(module.subprocess, 'run', run)
    return module.UvcControls(binary=binary), values, writes


def test_manual_exposure_readback_and_one_click_reset(monkeypatch, tmp_path):
    controls, values, writes = fake_uvc(monkeypatch, tmp_path)
    initial = controls.status()
    assert initial['device']['name'] == 'USB 2.0 Camera'
    assert initial['mode'] == 'auto'
    assert initial['exposure_100us']['minimum'] == 1
    assert initial['gain']['maximum'] == 100

    manual = controls.apply('manual', 100, 12)
    assert manual['mode'] == 'manual'
    assert manual['exposure_100us']['current_value'] == 100
    assert manual['gain']['current_value'] == 12
    assert writes[:3] == [('auto-exposure-mode', 1), ('exposure-time-abs', 100), ('gain', 12)]

    restored = controls.reset()
    assert restored['mode'] == 'auto'
    assert values == {'auto-exposure-mode': 8, 'exposure-time-abs': 157, 'gain': 0}
    assert writes[-4:] == [('auto-exposure-mode', 1), ('exposure-time-abs', 157),
                           ('gain', 0), ('auto-exposure-mode', 8)]


def test_out_of_range_rejected_before_changing_camera(monkeypatch, tmp_path):
    controls, _, writes = fake_uvc(monkeypatch, tmp_path)
    with pytest.raises(module.UvcControlError, match='范围'):
        controls.apply('manual', 5001, 0)
    assert not writes
    with pytest.raises(module.UvcControlError, match='增益'):
        controls.apply('manual', 100, None)
    assert not writes


def test_ignored_hardware_write_is_not_reported_as_success(monkeypatch, tmp_path):
    controls, values, _ = fake_uvc(monkeypatch, tmp_path)
    original_run = module.subprocess.run

    def ignore_gain(command, **kwargs):
        if '-s' in command and command[-1].startswith('gain='):
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        return original_run(command, **kwargs)

    monkeypatch.setattr(module.subprocess, 'run', ignore_gain)
    with pytest.raises(module.UvcControlError, match='未生效'):
        controls.apply('manual', 100, 20)
    assert values['gain'] == 0
