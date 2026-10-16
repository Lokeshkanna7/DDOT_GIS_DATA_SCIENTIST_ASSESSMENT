"""DDOT Phase 3 exception investigation — NON-DESTRUCTIVE.

Produces ranked *candidates*, never assigns FHWA classes to the source CSV.
Example: py resolve_exceptions.py --limit 10
Full:    py resolve_exceptions.py --all --checkpoint-every 10
Resume:  py resolve_exceptions.py --all --resume
Offline: py resolve_exceptions.py --limit 10 --dry-run
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import logging
import math
import os
import re
import time
from collections import Counter
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
LRS_URL = ('https://rh.dcgis.dc.gov/dcgis/rest/services/DDOT/RoadwayBlocks/'
           'MapServer/exts/DdotLrsExtensions/getPointOnRoute')
CLASS_URL = ('https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/'
             'Transportation_WebMercator/MapServer/48/query')
FHWA = {1: 'Interstate', 2: 'Other Freeway or Expressway',
        3: 'Other Principal Arterial', 4: 'Minor Arterial', 5: 'Major Collector',
        6: 'Minor Collector', 7: 'Local'}
COLUMNS = ['OBJECTID', 'TrackingNumber', 'LocationID', 'PermitAddress', 'OriginalStatus',
           'CandidateRank', 'CandidateSource', 'RoadOBJECTID', 'RouteName', 'RouteID',
           'BlockKey', 'BlockName', 'RoadType', 'FHWAClassCandidate', 'FHWAClassNameCandidate',
           'DistanceMeters', 'StreetLRSRoute', 'StreetLRSBlockKey', 'StreetLRSOffsetMeters',
           'AlleyLRSRoute', 'AlleyLRSOffsetMeters', 'NameMatch', 'BlockNumberMatch',
           'RouteIDMatch', 'BlockKeyMatch', 'Score', 'Evidence', 'Recommendation', 'DataQuality']
SUMMARY_COLS = ['OBJECTID', 'TrackingNumber', 'LocationID', 'PermitAddress', 'OriginalStatus',
                'CandidatesFound', 'TopRoad', 'TopFHWAClass', 'TopScore', 'TopRecommendation',
                'InvestigationStatus', 'ErrorMessage']
SWAPS = {'ROAD':'RD', 'STREET':'ST', 'AVENUE':'AVE', 'PLACE':'PL',
         'BOULEVARD':'BLVD', 'DRIVE':'DR', 'COURT':'CT', 'LANE':'LN',
         'TERRACE':'TER', 'CIRCLE':'CIR', 'PARKWAY':'PKWY',
         'NORTHWEST':'NW', 'NORTHEAST':'NE', 'SOUTHWEST':'SW', 'SOUTHEAST':'SE'}
LOG = logging.getLogger('ddot_exceptions')


def norm(s):
    s = re.sub(r'[^A-Z0-9 ]', ' ', str(s or '').upper())
    return ' '.join(SWAPS.get(x, x) for x in s.split())


def route_in_address(address, route):
    a, r = norm(address), norm(route)
    return bool(r and re.search(r'(?<!\w)' + re.escape(r) + r'(?!\w)', a))


def number_in_block(address, block):
    a, b = norm(address), norm(block)
    if not a or not b:
        return None
    if a == b or a in b or b in a:
        return True
    number = re.match(r'^(\d+)\b', a)
    bounds = re.match(r'^(\d+)\s+(?:TO\s+)?(\d+)\s+BLOCK OF\b', b)
    if number and bounds:
        return int(bounds[1]) <= int(number[1]) <= int(bounds[2])
    # A named intersection/bridge or atypical address is not a verified number match.
    return None


def missing(v):
    return v is None or str(v).strip().lower() in ('', 'nan', 'none', 'null')


def valid_float(v):
    try:
        return None if missing(v) else float(v)
    except (TypeError, ValueError):
        return None


def point_line_distance_m(lon, lat, paths):
    """Local equirectangular approximation, appropriate for short DC road segments.
    Reports metres, not cadastral-grade geodesic measurement.
    """
    coslat = math.cos(math.radians(lat))
    scale_x = 111320.0 * coslat
    scale_y = 111132.0
    closest = float('inf')
    for path in paths or []:
        points = [(float(pt[0]), float(pt[1])) for pt in path if len(pt) >= 2]
        for p, q in zip(points, points[1:]):
            ax, ay = (p[0]-lon)*scale_x, (p[1]-lat)*scale_y
            bx, by = (q[0]-lon)*scale_x, (q[1]-lat)*scale_y
            dx, dy = bx-ax, by-ay
            t = max(0.0, min(1.0, -(ax*dx+ay*dy)/(dx*dx+dy*dy))) if dx*dx+dy*dy else 0
            closest = min(closest, math.hypot(ax+t*dx, ay+t*dy))
        if len(points) == 1:
            closest = min(closest, math.hypot((points[0][0]-lon)*scale_x,
                                                    (points[0][1]-lat)*scale_y))
    return None if not math.isfinite(closest) else round(closest, 2)


def read_csv(path):
    with path.open('r', encoding='utf-8-sig', newline='') as file:
        return list(csv.DictReader(file))


def file_sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)


def write_csv(path, rows, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def http_session():
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=['POST', 'GET'], respect_retry_after_header=True)
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.headers['User-Agent'] = 'DDOT-GIS-exception-review/1.0'
    return s


def arcjson(response, label):
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as ex:
        raise RuntimeError(f'{label} returned non-JSON: {response.text[:130]}') from ex
    if not isinstance(data, dict) or 'error' in data:
        raise RuntimeError(f'{label} invalid/error: {str(data)[:220]}')
    return data


def lookup_lrs(s, lon, lat, route_type, radius):
    form = {'x': lon, 'y': lat, 'inSR': 4326, 'outSR': 4326,
            'routeType': route_type, 'searchRadius': radius,
            'returnNearestMatchOnly': 'true', 'includeSectionEvents': 'false', 'f':'json'}
    data = arcjson(s.post(LRS_URL, data=form, timeout=60), 'LRS')
    if not isinstance(data.get('pointOnRoutes'), list):
        raise RuntimeError('LRS returned documentation instead of pointOnRoutes; verify /getPointOnRoute')
    return data['pointOnRoutes'][0] if data['pointOnRoutes'] else None


def query_road_candidates(s, lon, lat, radius):
    params = {'where': '1=1', 'geometry': f'{lon},{lat}', 'geometryType':'esriGeometryPoint',
              'inSR':4326, 'spatialRel':'esriSpatialRelIntersects', 'distance': radius,
              'units':'esriSRUnit_Meter', 'outFields':
              'OBJECTID,ROUTENAME,ROUTEID,BLOCKKEY,BLOCK_NAME,ROADTYPE,FHWAFUNCTIONALCLASS',
              'returnGeometry':'true', 'outSR':4326, 'f':'json',
              'resultRecordCount':500, 'orderByFields':'OBJECTID ASC'}
    data = arcjson(s.get(CLASS_URL, params=params, timeout=60), 'Functional-class layer')
    if not isinstance(data.get('features'), list):
        raise RuntimeError(f'Layer response missing features: {list(data)[:9]}')
    if data.get('exceededTransferLimit'):
        raise RuntimeError('Functional-class candidates exceeded service transfer limit')
    return data['features']


def make_candidate(row, feature, street, alley, lon, lat):
    a = feature.get('attributes') or {}
    distance = point_line_distance_m(lon, lat, (feature.get('geometry') or {}).get('paths'))
    route = a.get('ROUTENAME') or ''
    block = a.get('BLOCK_NAME') or ''
    key = str(a.get('BLOCKKEY') or '')
    rid = str(a.get('ROUTEID') or '')
    name_match = route_in_address(row.get('WorkLocationFullAddress'), route)
    number_match = number_in_block(row.get('WorkLocationFullAddress'), block) if name_match else None
    route_match = bool(street and str(street.get('routeId')) == rid)
    key_match = bool(street and street.get('blockKey') and str(street['blockKey']) == key)
    raw_class = a.get('FHWAFUNCTIONALCLASS')
    code = int(raw_class) if str(raw_class).isdigit() else None
    roadtype = str(a.get('ROADTYPE') or '')
    quality = []
    if code not in FHWA: quality.append('missing/invalid FHWA class')
    if roadtype not in ('1', 'Street'): quality.append('non-street road type')
    if distance is None: quality.append('geometry distance unavailable')
    score = 0
    evidence = []
    if name_match: score += 45; evidence.append('route name appears in permit address')
    else: evidence.append('route name differs from permit address')
    if number_match is True: score += 20; evidence.append('address number/block verified')
    elif number_match is False: score -= 25; evidence.append('address number outside block')
    if route_match: score += 15; evidence.append('LRS route ID agrees')
    if key_match: score += 15; evidence.append('LRS BLOCKKEY agrees')
    if distance is not None:
        score += max(0, 20 - distance/3)
        evidence.append(f'centerline distance {distance:.2f}m')
        if distance > 40: score -= 20
    if roadtype not in ('1','Street'): score -= 35
    if code not in FHWA: score -= 35
    if not name_match and not (route_match and key_match): score -= 20
    if number_match is False: score -= 5
    is_special = bool(re.search(r'\b(?:BRIDGE|TUNNEL|OVERPASS|UNDERPASS)\b',
                                str(row.get('WorkLocationFullAddress') or ''), re.I))
    alley_off = valid_float(alley.get('offsetInMeters')) if alley else None
    if alley_off is not None and distance is not None and alley_off + 3 < distance:
        score -= 20
        evidence.append('alley nearer than proposed street')
    if is_special: score -= 8; evidence.append('special structure requires GIS confirmation')
    score = round(max(0, min(100, score)), 1)
    # Score ranks candidates only. Never represents a verified class or auto approval.
    if quality:
        recommendation = 'REVIEW_DATA_QUALITY'
    elif is_special:
        recommendation = 'REVIEW_BRIDGE_OR_STRUCTURE'
    elif alley_off is not None and distance is not None and alley_off + 3 < distance:
        recommendation = 'REVIEW_POSSIBLE_ALLEY'
    elif name_match and number_match is not False and (distance is not None and distance <= 15):
        recommendation = 'STRONG_CANDIDATE_VERIFY_MANUALLY'
    elif route_match and key_match:
        recommendation = 'LRS_AGREES_VERIFY_ADDRESS'
    else:
        recommendation = 'REVIEW_AMBIGUOUS'
    return {'OBJECTID':row['OBJECTID'], 'TrackingNumber':row['TrackingNumber'],
            'LocationID':row['LocationID'], 'PermitAddress':row.get('WorkLocationFullAddress',''),
            'OriginalStatus':row.get('MatchStatus',''), 'CandidateRank':None,
            'CandidateSource':'Roadway Functional Classification Layer 48 / spatial search',
            'RoadOBJECTID':a.get('OBJECTID',''), 'RouteName':route, 'RouteID':rid,
            'BlockKey':key, 'BlockName':block, 'RoadType':roadtype,
            'FHWAClassCandidate':code if code in FHWA else '',
            'FHWAClassNameCandidate':FHWA.get(code,''), 'DistanceMeters':distance,
            'StreetLRSRoute':street.get('routeName','') if street else '',
            'StreetLRSBlockKey':street.get('blockKey','') if street else '',
            'StreetLRSOffsetMeters':street.get('offsetInMeters','') if street else '',
            'AlleyLRSRoute':alley.get('routeName','') if alley else '',
            'AlleyLRSOffsetMeters':alley.get('offsetInMeters','') if alley else '',
            'NameMatch':name_match, 'BlockNumberMatch':number_match,
            'RouteIDMatch':route_match, 'BlockKeyMatch':key_match,
            'Score':score, 'Evidence':'; '.join(evidence), 'Recommendation':recommendation,
            'DataQuality':'; '.join(quality)}


def investigate(row, s, cfg):
    lon = valid_float(row.get('Longitude'))
    lat = valid_float(row.get('Latitude'))
    if lon is None or lat is None:
        lon = valid_float(row.get('GeometryLongitude'))
        lat = valid_float(row.get('GeometryLatitude'))
    summary = {'OBJECTID':row['OBJECTID'], 'TrackingNumber':row['TrackingNumber'],
               'LocationID':row['LocationID'], 'PermitAddress':row.get('WorkLocationFullAddress',''),
               'OriginalStatus':row.get('MatchStatus',''), 'CandidatesFound':0, 'TopRoad':'',
               'TopFHWAClass':'', 'TopScore':'', 'TopRecommendation':'',
               'InvestigationStatus':'', 'ErrorMessage':''}
    if lon is None or lat is None or not (-180 < lon < 180 and -90 < lat < 90):
        summary['InvestigationStatus'] = 'BAD_COORDINATES'
        return {'summary':summary,'candidates':[]}
    if cfg['dry_run']:
        summary['InvestigationStatus'] = 'DRY_RUN_NO_API_REQUESTS'
        return {'summary':summary,'candidates':[]}
    try:
        street = lookup_lrs(s, lon, lat, 'Street', cfg['lrs_radius'])
        time.sleep(cfg['pause'])
        alley = lookup_lrs(s, lon, lat, 'Alley', cfg['lrs_radius'])
        time.sleep(cfg['pause'])
        features = query_road_candidates(s, lon, lat, cfg['search_radius'])
        time.sleep(cfg['pause'])
        candidates = [make_candidate(row, f, street, alley, lon, lat) for f in features]
        candidates.sort(key=lambda c: (-c['Score'], c['DistanceMeters'] if c['DistanceMeters'] is not None else 1e9,
                                      str(c['RoadOBJECTID'])))
        candidates = candidates[:cfg['max_candidates']]
        for rank, candidate in enumerate(candidates, 1):
            candidate['CandidateRank'] = rank
        summary['CandidatesFound'] = len(features)
        summary['InvestigationStatus'] = 'CANDIDATES_FOUND' if candidates else 'NO_CANDIDATES_WITHIN_RADIUS'
        if candidates:
            top = candidates[0]
            summary.update({'TopRoad':top['RouteName'], 'TopFHWAClass':top['FHWAClassCandidate'],
                            'TopScore':top['Score'], 'TopRecommendation':top['Recommendation']})
        return {'summary':summary, 'candidates':candidates}
    except (requests.RequestException, RuntimeError, ValueError, TypeError) as e:
        summary['InvestigationStatus']='API_ERROR'
        summary['ErrorMessage']=str(e)[:350]
        return {'summary':summary,'candidates':[]}


def verify_input(enriched, review):
    required = {'OBJECTID','TrackingNumber','LocationID','MatchStatus','WorkLocationFullAddress'}
    for name, data in [('enriched',enriched),('review',review)]:
        if not data or not required.issubset(data[0]):
            raise ValueError(f'{name} CSV missing data/columns: {required}')
    main_ids = [r['OBJECTID'] for r in enriched]
    review_ids = [r['OBJECTID'] for r in review]
    if len(set(main_ids)) != len(main_ids) or len(set(review_ids)) != len(review_ids):
        raise ValueError('Duplicate OBJECTID in input')
    main = {r['OBJECTID']:r for r in enriched}
    for r in review:
        original = main.get(r['OBJECTID'])
        if not original:
            raise ValueError(f"Review OBJECTID {r['OBJECTID']} missing from main CSV")
        for field in ('TrackingNumber','LocationID','MatchStatus'):
            if original[field] != r[field]:
                raise ValueError(f"Input mismatch for OBJECTID {r['OBJECTID']}, field {field}")
        if not missing(original.get('FHWAClass')):
            raise ValueError(f"Review location {r['OBJECTID']} already has FHWA class")
    return Counter(r['MatchStatus'] for r in review)


def export(results, folder):
    candidates = [cand for entry in results for cand in entry['candidates']]
    summaries = [entry['summary'] for entry in results]
    write_csv(folder/'exception_candidates.csv', candidates, COLUMNS)
    write_csv(folder/'exception_investigation_summary.csv', summaries, SUMMARY_COLS)
    flagged = [r for r in summaries if r['InvestigationStatus'] in
               ('API_ERROR', 'BAD_COORDINATES', 'NO_CANDIDATES_WITHIN_RADIUS')]
    write_csv(folder/'exception_followup.csv', flagged, SUMMARY_COLS)
    return len(candidates), len(flagged)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enriched', type=Path, default=ROOT/'output'/'permits_enriched.csv')
    parser.add_argument('--review', type=Path, default=ROOT/'output'/'permits_enriched_review.csv')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'output')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--limit', type=int)
    mode.add_argument('--all', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--checkpoint-every', type=int, default=10)
    parser.add_argument('--search-radius', type=int, default=75, help='spatial candidates in metres')
    parser.add_argument('--lrs-radius', type=int, default=100, help='LRS radius in metres, maximum 200')
    parser.add_argument('--max-candidates', type=int, default=5)
    parser.add_argument('--pause', type=float, default=0.3)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    if args.checkpoint_every < 1 or args.max_candidates < 1 or args.pause < 0:
        parser.error('checkpoint frequency/max candidates must be positive; pause nonnegative')
    if not 1 <= args.lrs_radius <= 200 or not 1 <= args.search_radius <= 200:
        parser.error('LRS/spatial radii must be 1–200 metres')
    if not args.enriched.is_file() or not args.review.is_file():
        parser.error('Input CSV missing; put both latest full-run files in output/ or pass explicit paths')
    all_rows, review = read_csv(args.enriched), read_csv(args.review)
    counts = verify_input(all_rows, review)
    review.sort(key=lambda r: (int(r['OBJECTID']) if r['OBJECTID'].isdigit() else r['OBJECTID']))
    target = review if args.all else review[:args.limit]
    cfg = {'dry_run':args.dry_run, 'search_radius':args.search_radius,
           'lrs_radius':args.lrs_radius,'max_candidates':args.max_candidates,'pause':args.pause}
    input_meta = {'main_sha256':file_sha(args.enriched),'review_sha256':file_sha(args.review),
                  'target_ids':[r['OBJECTID'] for r in target], 'config':cfg,
                  'schema_version':1}
    checkpoint = args.output_dir/'exception_progress.json'
    if args.resume:
        if not checkpoint.exists():
            parser.error('No checkpoint exists for --resume')
        payload = json.loads(checkpoint.read_text(encoding='utf-8'))
        if payload.get('meta') != input_meta:
            parser.error('Checkpoint input/config differs; unsafe resume refused. Use matching options.')
        results = payload.get('results',[])
        if len(results)>len(target):
            parser.error('Checkpoint longer than target')
        print(f'Resuming {len(results)}/{len(target)} reviewed locations')
    else:
        if checkpoint.exists():
            parser.error('Existing exception_progress.json found. Use --resume or archive/delete that file explicitly.')
        results=[]
    print(f'Review input: {len(review)} records; current target: {len(target)}; statuses: {dict(counts)}')
    session = http_session()
    try:
        for idx in range(len(results), len(target)):
            row = target[idx]
            outcome = investigate(row, session, cfg)
            results.append(outcome)
            print(f"[{idx+1}/{len(target)}] ID {row['OBJECTID']}: "
                  f"{outcome['summary']['InvestigationStatus']}, "
                  f"{len(outcome['candidates'])} ranked candidates")
            if (idx+1) % args.checkpoint_every == 0:
                atomic_text(checkpoint, json.dumps({'meta':input_meta,'results':results}, ensure_ascii=False))
                export(results,args.output_dir)
    except KeyboardInterrupt:
        print('\nInterrupted; saving checkpoint before exit.')
        raise
    finally:
        atomic_text(checkpoint, json.dumps({'meta':input_meta,'results':results}, ensure_ascii=False))
        n, flagged=export(results,args.output_dir)
        print(f'Saved {len(results)} investigations, {n} ranked candidates, {flagged} follow-ups')
    print('Original permits_enriched.csv and permits_enriched_review.csv were NOT changed.')
    print('Candidates must be verified manually before any change to FHWAClass.')

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    main()
