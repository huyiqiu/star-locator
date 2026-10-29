"""Intensity centroids -> projective grid -> error-correcting ID -> planar PnP.

Four corner anchors must be visible. All 12 data cells are validated, including
empty cells. Work is bounded; clutter never becomes an unbounded quad search.
"""
from __future__ import annotations
import time
import cv2
import numpy as np
from .family import ANCHORS, CELLS, CODES, FAMILY_NAME, bits, grid
from .models import DetectorConfig, Intrinsics

CANON_CORNERS = np.array([[0, 0], [3, 0], [3, 3], [0, 3]], np.float32)
ALL_GRID = np.array([[c, r] for r in range(4) for c in range(4)], np.float32)


def camera_matrix(intr: Intrinsics, width: int, height: int):
    # Scaling a calibration is valid only for the same uncropped aspect ratio.
    if abs(width / height - intr.width / intr.height) > .01:
        raise ValueError('calibration_aspect_mismatch')
    sx, sy = width / intr.width, height / intr.height
    return np.array([[intr.fx*sx, 0, intr.cx*sx], [0, intr.fy*sy, intr.cy*sy], [0, 0, 1.]], np.float64)


def undistortion_roundtrip_error(raw, undistorted, K, dist):
    """How well the inverse-distorted points reproduce their measured pixels."""
    xy = np.column_stack(((undistorted[:, 0]-K[0, 2])/K[0, 0],
                          (undistorted[:, 1]-K[1, 2])/K[1, 1],
                          np.ones(len(undistorted))))
    if not np.isfinite(xy).all():
        return None
    projected, _ = cv2.projectPoints(xy, np.zeros(3), np.zeros(3), K, dist)
    errors = np.linalg.norm(projected.reshape(-1, 2)-raw, axis=1)
    return float(np.max(errors)) if np.isfinite(errors).all() else None


def blobs_from_image(image, cfg):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    smooth = cv2.GaussianBlur(gray, (cfg.blur_size, cfg.blur_size), 0) if cfg.blur_size > 1 else gray
    if cfg.threshold_mode == 'otsu':
        threshold, binary = cv2.threshold(smooth, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif cfg.threshold_mode == 'adaptive':
        binary = cv2.adaptiveThreshold(smooth, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, -8)
        threshold = None
    else:
        threshold, binary = cv2.threshold(smooth, cfg.threshold, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    accepted = []
    h, w = gray.shape
    for contour in contours:
        area = cv2.contourArea(contour)
        if not cfg.min_area <= area <= cfg.max_area:
            continue
        perimeter = cv2.arcLength(contour, True)
        circularity = 4 * np.pi * area / max(perimeter**2, 1.)
        if circularity < cfg.min_circularity:
            continue
        x, y, bw, bh = cv2.boundingRect(contour)
        if x <= 0 or y <= 0 or x+bw >= w or y+bh >= h or max(bw, bh)/max(1, min(bw, bh)) > 4:
            continue
        mask = np.zeros((bh, bw), np.uint8)
        cv2.drawContours(mask, [contour-np.array([[[x, y]]])], -1, 255, -1)
        patch = gray[y:y+bh, x:x+bw].astype(np.float64)
        # Positive weights suppress local background; no integer centroid rounding.
        weights = np.maximum(patch-float(np.percentile(patch, 10)), 1) * (mask > 0)
        yy, xx = np.indices(weights.shape)
        total = weights.sum()
        accepted.append({'x': float(x+(weights*xx).sum()/total), 'y': float(y+(weights*yy).sum()/total),
                         'area': float(area), 'radius': float(np.sqrt(area/np.pi)), 'circularity': float(circularity),
                         'mean_intensity': float(patch[mask > 0].mean())})
    accepted.sort(key=lambda b: b['area']*b['circularity'], reverse=True)
    saturated = len(accepted) > cfg.max_blobs
    return gray, binary, accepted[:cfg.max_blobs], threshold, saturated


def candidate_quads(points):
    n = len(points)
    if n < 6:
        return []
    d = np.linalg.norm(points[:, None]-points[None, :], axis=2)
    np.fill_diagonal(d, np.inf)
    scale = float(np.median(np.min(d, axis=1)))
    sets = [tuple(range(n))] + [tuple(range(k)) for k in range(6, min(n, 24)+1)]
    for factor in (1.7, 2.6):
        remaining = set(range(n))
        while remaining:
            seed = remaining.pop()
            stack, component = [seed], [seed]
            while stack:
                k = stack.pop()
                neighbors = {int(j) for j in np.where(d[k] < scale*factor)[0]} & remaining
                remaining -= neighbors
                stack.extend(neighbors)
                component.extend(neighbors)
            if 6 <= len(component) <= 40:
                sets.append(tuple(sorted(component)))
    # Local neighborhoods recover boards in clutter and when dilation joins boards.
    for i in range(n):
        order = np.argsort(d[i])
        for k in range(6, min(n, 16)+1):
            if n >= k:
                sets.append(tuple(sorted([i]+order[:k-1].tolist())))
    # Sparse boards may have no bright point near their center. Neighborhoods
    # centered between two dots retain all four anchors when two boards coexist.
    for i in range(n):
        for j in np.argsort(d[i])[:min(n-1,16)]:
            if j<=i or not scale*1.5<d[i,j]<scale*6:
                continue
            center=(points[i]+points[j])*.5
            order=np.argsort(np.linalg.norm(points-center,axis=1))
            for k in range(6,min(n,12)+1):
                sets.append(tuple(sorted(order[:k].tolist())))
    quads, seen = [], set()
    for subset in dict.fromkeys(sets):
        p = points[list(subset)].astype(np.float32)
        hull = cv2.convexHull(p)
        perimeter = cv2.arcLength(hull, True)
        for epsilon in (.025, .055):
            poly = cv2.approxPolyDP(hull, epsilon*perimeter, True).reshape(-1, 2)
            if len(poly) != 4:
                continue
            center = poly.mean(axis=0)
            poly = poly[np.argsort(np.arctan2(poly[:, 1]-center[1], poly[:, 0]-center[0]))]
            area = cv2.contourArea(poly)
            edges = np.linalg.norm(poly-np.roll(poly, -1, axis=0), axis=1)
            if area < 180 or min(edges) < 13 or max(edges)/min(edges) > 6:
                continue
            key = tuple(sorted(int(np.argmin(np.linalg.norm(points-v, axis=1))) for v in poly))
            if key in seen:
                continue
            seen.add(key)
            quads.append(poly)
    return quads[:350]


def _refine_pose(obj, img, K, dist, rvec, tvec):
    try:
        rvec, tvec = cv2.solvePnPRefineLM(obj, img, K, dist, rvec.copy(), tvec.copy())
    except cv2.error:
        return None
    R = cv2.Rodrigues(rvec)[0]
    if not np.isfinite(R).all() or not np.isfinite(tvec).all():
        return None
    if np.min((R@obj.T+tvec)[2]) <= 0 or R[2, 2] <= 0:
        return None
    projected = cv2.projectPoints(obj, rvec, tvec, K, dist)[0].reshape(-1, 2)
    rms = float(np.sqrt(np.mean(np.sum((projected-img)**2, axis=1))))
    return (rms, rvec, tvec) if np.isfinite(rms) else None


def _pose(obj, img, K, dist, max_error):
    try:
        result = cv2.solvePnPGeneric(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE)
    except cv2.error:
        return None
    if not result[0]:
        return None
    raw_candidates = []
    for rvec, tvec in zip(result[1], result[2]):
        R = cv2.Rodrigues(rvec)[0]
        if not np.isfinite(R).all() or not np.isfinite(tvec).all():
            continue
        if np.min((R@obj.T+tvec)[2]) <= 0 or R[2, 2] <= 0:
            continue
        projected = cv2.projectPoints(obj, rvec, tvec, K, dist)[0].reshape(-1, 2)
        rms = float(np.sqrt(np.mean(np.sum((projected-img)**2, axis=1))))
        raw_candidates.append((rms, rvec, tvec))
    if not raw_candidates:
        return None
    raw_candidates.sort(key=lambda v: v[0])
    raw_separation = 0.
    if len(raw_candidates) > 1:
        R0, R1 = (cv2.Rodrigues(c[1])[0] for c in raw_candidates[:2])
        raw_separation = float(np.degrees(np.arccos(np.clip((np.trace(R0.T@R1)-1)/2, -1, 1))))
    # IPPE roots are algebraic candidates. Refine both before comparing them:
    # near-frontal views can produce two raw roots that collapse to one minimum.
    refined_candidates = [c for c in (_refine_pose(obj, img, K, dist, r[1], r[2])
                                     for r in raw_candidates) if c is not None]
    if not refined_candidates:
        return None
    refined_candidates.sort(key=lambda v: v[0])
    rms, rvec, tvec = refined_candidates[0]
    if rms > max_error:
        return None
    rotation_separation = 0.
    if len(refined_candidates) > 1:
        R0, R1 = (cv2.Rodrigues(c[1])[0] for c in refined_candidates[:2])
        rotation_separation = float(np.degrees(np.arccos(np.clip((np.trace(R0.T@R1)-1)/2, -1, 1))))
    ambiguous = (len(refined_candidates) > 1
                 and refined_candidates[1][0]-rms < .25
                 and rotation_separation > 3)
    R = cv2.Rodrigues(rvec)[0]
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, tvec.ravel()
    inverse = np.eye(4)
    inverse[:3, :3], inverse[:3, 3] = R.T, -R.T@tvec.ravel()
    return {'T_camera_marker': T.tolist(), 'T_marker_camera': inverse.tolist(),
            'rvec': rvec.ravel().tolist(), 'tvec_mm': tvec.ravel().tolist(),
            'camera_position_marker_mm': inverse[:3, 3].tolist(),
            'distance_mm': float(np.linalg.norm(tvec)), 'optical_depth_mm': float(tvec[2, 0]),
            'reprojection_rms_px': rms, 'ambiguous': bool(ambiguous),
            'ippe_candidate_rms_px': [c[0] for c in raw_candidates],
            'candidate_angle_separation_deg': raw_separation,
            'refined_candidate_rms_px': [c[0] for c in refined_candidates],
            'refined_candidate_angle_separation_deg': rotation_separation,
            'point_count': len(obj)}


def render_glow(height, width, blobs):
    """Match full-frame Gaussian glow while filtering only its nonzero region.

    The 24 px zero margin exceeds the sigma=6 uint8 Gaussian kernel radius,
    including for dots near an image edge. Empty sets avoid filtering entirely.
    """
    output = np.zeros((height, width, 3), np.uint8)
    if not blobs:
        return output
    circles = [(round(b['x']), round(b['y']), max(2, round(b['radius']))) for b in blobs]
    margin = 24
    left = max(0, min(x-r for x,y,r in circles)-margin)
    top = max(0, min(y-r for x,y,r in circles)-margin)
    right = min(width, max(x+r for x,y,r in circles)+margin+1)
    bottom = min(height, max(y+r for x,y,r in circles)+margin+1)
    roi = np.zeros((bottom-top, right-left, 3), np.uint8)
    for x,y,r in circles:
        cv2.circle(roi, (x-left,y-top), r, (230,255,210), -1, cv2.LINE_AA)
    blurred = cv2.GaussianBlur(roi, (0,0), 6)
    output[top:bottom,left:right] = cv2.addWeighted(roi, 1., blurred, 1.8, 0)
    return output


def detect(image, cfg: DetectorConfig, intrinsics: Intrinsics | None = None):
    started = time.perf_counter()
    gray, binary, blobs, threshold, overflow = blobs_from_image(image, cfg)
    h, w = gray.shape
    raw = np.array([[b['x'], b['y']] for b in blobs], dtype=np.float64).reshape(-1, 2)
    warnings, K, dist = [], None, None
    if overflow:
        warnings.append('blob_limit_reached')
    if intrinsics:
        try:
            K = camera_matrix(intrinsics, w, h)
            dist = np.array(intrinsics.distortion, dtype=np.float64)
        except ValueError as exc:
            warnings.append(str(exc))
    points = cv2.undistortPoints(raw[:, None, :], K, dist, P=K).reshape(-1, 2) if K is not None and len(raw) else raw
    undistortion_error = None
    if K is not None and len(raw):
        try:
            undistortion_error = undistortion_roundtrip_error(raw, points, K, dist)
            if undistortion_error is None or undistortion_error > 2.5:
                warnings.append('undistortion_unstable')
        except cv2.error:
            warnings.append('undistortion_unstable')
    if not intrinsics or not intrinsics.calibrated:
        warnings.append('intrinsics_not_calibrated')
    quads = candidate_quads(points)
    matches = []
    code_hypotheses = []
    rejected_codes = 0
    for quad in quads:
        H = cv2.getPerspectiveTransform(CANON_CORNERS, quad.astype(np.float32))
        invertible, invH = cv2.invert(H, flags=cv2.DECOMP_LU)
        if not invertible:
            continue
        local = cv2.perspectiveTransform(points.astype(np.float32)[:, None, :], invH).reshape(-1, 2)
        nearest = np.rint(local).astype(int)
        residual = np.linalg.norm(local-nearest, axis=1)
        indices = np.where((nearest >= 0).all(axis=1) & (nearest <= 3).all(axis=1) & (residual < cfg.grid_tolerance))[0]
        base_assignments = {}
        for i in sorted(indices, key=lambda i: residual[i]):
            c, r = nearest[i]
            base_assignments.setdefault((int(r), int(c)), int(i))
        base_observed = np.zeros((4, 4), np.uint8)
        for r, c in base_assignments:
            base_observed[r, c] = 1
        if not all(base_observed[r, c] for r, c in ANCHORS):
            continue
        assignments = base_assignments
        # A quarter-turn preserves grid residuals. Rectify each quad once,
        # then rotate the discrete cells to test all four ID directions.
        for k in range(4):
            if k:
                assignments = {(3-c, r):i for (r, c), i in assignments.items()}
            observed = np.rot90(base_observed, k)
            code = bits(observed)
            distances = [(code ^ c).bit_count() for c in CODES]
            best = int(np.argmin(distances))
            if distances[best] > cfg.max_hamming or distances.count(distances[best]) != 1:
                rejected_codes += 1
                continue
            expected = grid(best+1)
            pairs = [(rc, i) for rc, i in assignments.items() if expected[rc]]
            if len(pairs) < 6:
                continue
            # Keep code-consistent hypotheses even if their geometry later
            # fails. A partial quad must not masquerade as another ID after
            # the complete marker has been rejected for crooked dot placement.
            code_hypotheses.append(frozenset(i for _, i in pairs))
            obj_grid = np.array([[c, r] for ((r, c), i) in pairs], np.float32)
            img_geo = np.array([points[i] for _, i in pairs], np.float32)
            # The cell pattern and all four anchors already agree. Fit every
            # matched dot so the user-facing reprojection limit is the actual
            # geometry gate; a fixed RANSAC threshold silently overrode it.
            refined, _ = cv2.findHomography(obj_grid, img_geo, 0)
            if refined is None:
                continue
            predicted = cv2.perspectiveTransform(obj_grid[:, None, :], refined).reshape(-1, 2)
            errors = np.linalg.norm(predicted-img_geo, axis=1)
            fit_rms = float(np.sqrt(np.mean(errors**2)))
            if fit_rms > cfg.max_reprojection_error_px or float(np.max(errors)) > 1.5*cfg.max_reprojection_error_px:
                continue
            # Validate absence after the fit too: reject points close to a forbidden cell.
            ids = [i for _, i in pairs]
            corners_raw = np.array([raw[assignments[rc]] for rc in ANCHORS])
            origin = cv2.perspectiveTransform(np.array([[[1.5, 1.5], [2.5, 1.5]]], np.float32), refined).reshape(-1, 2)
            direction = float(np.degrees(np.arctan2(*(origin[1]-origin[0])[::-1])) % 360)
            obj = np.array([[(c-1.5)*cfg.spacing_mm, (r-1.5)*cfg.spacing_mm, 0] for ((r, c), i) in pairs], np.float64)
            img_raw = np.array([raw[i] for _, i in pairs], np.float64)
            pose = None
            approximate = False
            if K is not None and intrinsics.calibrated:
                exact_complete = distances[best] == 0 and len(pairs) == int(np.sum(expected))
                pose_limit = max(cfg.max_reprojection_error_px, cfg.edge_pose_max_reprojection_error_px) if exact_complete else cfg.max_reprojection_error_px
                pose = _pose(obj, img_raw, K, dist, pose_limit)
                if pose and pose['reprojection_rms_px'] > cfg.max_reprojection_error_px:
                    approximate = True
            code_error = distances[best]
            quality = float(np.clip((1-code_error*.18)*np.exp(-fit_rms/3), 0, 1))
            if pose and pose['ambiguous']:
                quality *= .7
            matches.append({'id': best+1, 'family': FAMILY_NAME, 'code_hex': f'{CODES[best]:03X}',
                            'direction_deg': direction, 'quarter_turn': int(round(direction/90)) % 4,
                            'hamming_distance': code_error, 'corrected': bool(code_error),
                            'quality_score': quality, 'grid_rms_px': fit_rms,
                            'observed_grid': observed.tolist(), 'corners_px': corners_raw.tolist(),
                            'center_px': corners_raw.mean(axis=0).tolist(),
                            'dot_indices': ids, 'image_points_px': img_raw.tolist(),
                            'cells_rc': [list(rc) for rc, _ in pairs],
                            'pose': pose, 'pose_status': ('ambiguous' if pose and pose['ambiguous'] else 'approximate' if pose and approximate else 'valid' if pose else 'reprojection_rejected' if K is not None and intrinsics.calibrated else 'calibration_required')})
    # A partial quad can explain a subset of a larger real marker with a lower
    # fit error. Prefer the complete code before comparing fit residuals.
    matches.sort(key=lambda m: (m['hamming_distance'], -len(m['dot_indices']), m['grid_rms_px']))
    selected, used = [], set()
    for m in matches:
        occupied = set(m['dot_indices'])
        if any(len(other)>len(occupied) and len(other & occupied)>=len(occupied)-1
               for other in code_hypotheses):
            continue
        if len(occupied & used) > 2:
            continue
        selected.append(m)
        used |= occupied
        if len(selected) >= cfg.max_markers:
            break
    # When inversion diverges near the edge, decode directly in the image,
    # as a separate observation from pose estimation.  Require the full,
    # exact code; never use the error-correction allowance for this fallback.
    fallback_count = 0
    if len(selected) < cfg.max_markers and 'undistortion_unstable' in warnings:
        strict_raw = cfg.model_copy(update={'max_hamming': 0})
        raw_result, _ = detect(image, strict_raw, None)
        for m in raw_result['markers']:
            if m['hamming_distance'] != 0 or len(m['dot_indices']) != int(np.sum(grid(m['id']))):
                continue
            if len(set(m['dot_indices']) & used) > 2:
                continue
            m['geometry_source'] = 'raw_image'
            m['quality_score'] *= .5
            if K is not None and intrinsics and intrinsics.calibrated:
                obj = np.array([[(c-1.5)*cfg.spacing_mm, (r-1.5)*cfg.spacing_mm, 0]
                                for r, c in m['cells_rc']], np.float64)
                img_raw = np.array(m['image_points_px'], np.float64)
                try:
                    m['pose'] = _pose(obj, img_raw, K, dist, cfg.edge_pose_max_reprojection_error_px)
                except cv2.error:
                    m['pose'] = None
                m['pose_status'] = ('ambiguous' if m['pose'] and m['pose']['ambiguous']
                                    else 'approximate' if m['pose'] else 'reprojection_rejected')
            selected.append(m)
            used.update(m['dot_indices'])
            fallback_count += 1
            if len(selected) >= cfg.max_markers:
                break
    # Prefer unambiguous pose, then proximity to optical center.
    selected.sort(key=lambda m: (m['pose_status'] != 'valid', np.linalg.norm(np.array(m['center_px'])-[w/2, h/2])))
    for i, m in enumerate(selected):
        m['primary'] = i == 0
    glow = render_glow(h, w, [blobs[i] for i in used])
    all_glow = render_glow(h, w, blobs)
    result = {'family': FAMILY_NAME, 'width': w, 'height': h, 'markers': selected, 'blobs': blobs,
              'timing_ms': float((time.perf_counter()-started)*1000), 'threshold_used': threshold, 'image_statistics': {'min': int(gray.min()), 'max': int(gray.max()), 'mean': float(gray.mean()), 'p95': float(np.percentile(gray[::4,::4],95)), 'p99': float(np.percentile(gray[::4,::4],99))},
              'warnings': warnings, 'diagnostics': {'blob_count': len(blobs), 'candidate_quads': len(quads),
              'rejected_code_hypotheses': rejected_codes, 'matched_dots': len(used),
              'undistortion_roundtrip_max_px': undistortion_error,
              'raw_grid_fallback_count': fallback_count}}
    return result, {'raw': image, 'binary': cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR), 'glow': glow, 'blobs': all_glow}
