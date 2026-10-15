"""DDOT Phase 3: conservative, restartable permit-location FHWA enrichment.

First test: py enrich_road_classes.py --limit 100
Full run:   py enrich_road_classes.py --all
Resume:     py enrich_road_classes.py --all --resume

Uses DDOT LRS getPointOnRoute plus Functional Classification layer 48.
Do not treat ambiguous/bridge/alley proximity as a verified FHWA class.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import time
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
LRS_URL = ('https://rh.dcgis.dc.gov/dcgis/rest/services/DDOT/RoadwayBlocks/'
           'MapServer/exts/DdotLrsExtensions/getPointOnRoute')
CLASS_URL = ('https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/'
             'Transportation_WebMercator/MapServer/48/query')
CLASS_NAMES = {1: 'Interstate', 2: 'Other Freeway or Expressway',
               3: 'Other Principal Arterial', 4: 'Minor Arterial',
               5: 'Major Collector', 6: 'Minor Collector', 7: 'Local'}
RESULT_COLUMNS = ['MatchedRoadName', 'MatchedRoadType', 'MatchedRouteID', 'MatchedBlockKey',
                  'FHWAClass', 'FHWAClassName', 'StreetOffsetMeters', 'AlleyOffsetMeters',
                  'MatchMethod', 'MatchStatus', 'MatchNote']
REVIEW_PREFIX = ('REVIEW_', 'API_ERROR', 'MISSING_COORDINATES', 'NO_LRS_ROUTE')
LOG = logging.getLogger('ddot_enrich')


def session_with_retries():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=['GET', 'POST'])
    session.mount('https://', HTTPAdapter(max_retries=retry))
    session.headers.update({'User-Agent': 'DDOT-GIS-takehome/1.1'})
    return session


def response_json(response, operation):
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f'{operation} returned non-JSON: {response.text[:160]}') from exc
    if not isinstance(data, dict):
        raise RuntimeError(f'{operation} returned non-object JSON')
    if 'error' in data:
        raise RuntimeError(f'{operation} service error: {data["error"]}')
    return data


def lrs_lookup(session, lon, lat, road_type):
    params = {'x': lon, 'y': lat, 'inSR': 4326, 'outSR': 4326, 'routeType': road_type,
              'includeSectionEvents': 'false', 'returnNearestMatchOnly': 'true',
              'searchRadius': 30, 'f': 'json'}
    data = response_json(session.post(LRS_URL, data=params, timeout=45), 'LRS')
    if 'pointOnRoutes' not in data or not isinstance(data['pointOnRoutes'], list):
        raise RuntimeError(f'LRS returned metadata/unexpected schema: {list(data)[:12]}')
    matches = data['pointOnRoutes']
    return matches[0] if matches else None


def class_by_block(session, block_key, route_id):
    if not block_key:
        return None, 'NO_BLOCKKEY'
    escaped = str(block_key).replace("'", "''")
    params = {'where': f"BLOCKKEY = '{escaped}'", 'outFields': '*',
              'returnGeometry': 'false', 'f': 'json'}
    data = response_json(session.get(CLASS_URL, params=params, timeout=45), 'FHWA layer')
    if 'features' not in data:
        raise RuntimeError(f'FHWA layer returned unexpected schema: {list(data)[:12]}')
    attrs = [f.get('attributes', {}) for f in data['features']]
    matching = [a for a in attrs if str(a.get('ROUTEID')) == str(route_id)]
    if not matching:
        return None, 'NO_ROUTE_MATCH' if attrs else 'NO_CLASS_FEATURE'
    raw_codes = [a.get('FHWAFUNCTIONALCLASS') for a in matching]
    try:
        codes = {int(code) for code in raw_codes if code is not None}
    except (ValueError, TypeError):
        return None, 'INVALID_FHWA_CODE'
    if any(code not in CLASS_NAMES for code in codes):
        return None, 'UNEXPECTED_FHWA_CODE'
    if not codes or len(codes) != 1 or len(raw_codes) != len([c for c in raw_codes if c is not None]):
        return None, 'CONFLICTING_OR_MISSING_CLASS'
    selected = next(a for a in matching if a.get('FHWAFUNCTIONALCLASS') is not None)
    if str(selected.get('ROADTYPE')) not in ('1', 'Street'):
        return None, 'NOT_STREET_CLASS_FEATURE'
    return selected, 'MULTIPLE_SAME_CLASS' if len(matching) > 1 else 'OK'


def normalize(value):
    if value is None or pd.isna(value):
        return ''
    s = re.sub(r'[^A-Z0-9 ]', ' ', str(value).upper())
    swaps = {'ROAD': 'RD', 'STREET': 'ST', 'AVENUE': 'AVE', 'PLACE': 'PL',
             'BOULEVARD': 'BLVD', 'DRIVE': 'DR', 'COURT': 'CT', 'LANE': 'LN',
             'TERRACE': 'TER', 'CIRCLE': 'CIR', 'PARKWAY': 'PKWY',
             'NORTHWEST': 'NW', 'NORTHEAST': 'NE', 'SOUTHWEST': 'SW', 'SOUTHEAST': 'SE'}
    return ' '.join(swaps.get(t, t) for t in s.split())


def address_supports_route(address, route_name, block_name):
    """Match route name, then accept either a block description or house number in range.

    Returns (bool, explanation). Avoids approximate/partial route name matching.
    """
    addr, route, block = map(normalize, (address, route_name, block_name))
    if not addr or not route:
        return False, 'Missing work address or route name'
    if not re.search(r'(?<!\w)' + re.escape(route) + r'(?!\w)', addr):
        return False, 'Work address does not name the selected LRS route'
    if not block:
        return True, 'Exact street/route name (LRS block name unavailable)'
    if addr == block or addr in block or block in addr:
        return True, 'Work address matches LRS block description'
    # E.g. 3550 WATER STREET NW within 3500 - 3599 BLOCK OF WATER STREET NW.
    house = re.match(r'^(\d+)\s+', addr)
    bounds = re.match(r'^(\d+)\s+(?:TO\s+)?(\d+)\s+BLOCK OF\s+', block)
    if house and bounds:
        number, start, end = int(house[1]), int(bounds[1]), int(bounds[2])
        if start <= number <= end:
            return True, f'House number {number} falls in block range {start}-{end}'
        return False, f'House number {number} outside LRS block range {start}-{end}'
    # For a single-address block name, use the exact normalized address.
    return False, 'Street name matches but LRS block cannot be verified'


def empty_result():
    return {'MatchedRoadName': None, 'MatchedRoadType': None, 'MatchedRouteID': None,
            'MatchedBlockKey': None, 'FHWAClass': pd.NA, 'FHWAClassName': None,
            'StreetOffsetMeters': pd.NA, 'AlleyOffsetMeters': pd.NA,
            'MatchMethod': 'LRS + BLOCKKEY', 'MatchStatus': 'NOT_PROCESSED', 'MatchNote': None}


def classify_row(row, session, cache, pause):
    out = empty_result()
    lon, lat = row.get('Longitude'), row.get('Latitude')
    if pd.isna(lon) or pd.isna(lat):
        lon, lat = row.get('GeometryLongitude'), row.get('GeometryLatitude')
    if pd.isna(lon) or pd.isna(lat):
        out['MatchStatus'] = 'MISSING_COORDINATES'
        return out
    try:
        lon, lat = float(lon), float(lat)
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            out['MatchStatus'] = 'MISSING_COORDINATES'
            out['MatchNote'] = 'Coordinates outside longitude/latitude bounds'
            return out
        street = lrs_lookup(session, lon, lat, 'Street')
        time.sleep(pause)
        alley = lrs_lookup(session, lon, lat, 'Alley')
        time.sleep(pause)
        out['StreetOffsetMeters'] = street.get('offsetInMeters') if street else pd.NA
        out['AlleyOffsetMeters'] = alley.get('offsetInMeters') if alley else pd.NA
        address = str(row.get('WorkLocationFullAddress') or '')
        if not street and not alley:
            out['MatchStatus'] = 'NO_LRS_ROUTE'
            return out
        if re.search(r'\bALLEY\b', address, re.I):
            if alley:
                out.update({'MatchedRoadName': alley.get('routeName'), 'MatchedRoadType': 'Alley',
                            'MatchedRouteID': alley.get('routeId'), 'MatchStatus': 'ALLEY_UNCLASSIFIED',
                            'MatchNote': 'Explicit alley in source work-location address'})
            else:
                out['MatchStatus'] = 'REVIEW_ALLEY_NO_MATCH'
            return out
        if not street:
            out['MatchStatus'] = 'REVIEW_NO_STREET'
            out['MatchNote'] = 'No nearby street; inspect nearby alley and work address'
            return out
        out.update({'MatchedRoadName': street.get('routeName'),
                    'MatchedRoadType': street.get('routeType'),
                    'MatchedRouteID': street.get('routeId'),
                    'MatchedBlockKey': street.get('blockKey')})
        if re.search(r'\b(?:BRIDGE|TUNNEL|OVERPASS|UNDERPASS)\b', address, re.I):
            out['MatchStatus'] = 'REVIEW_SPECIAL_STRUCTURE'
            out['MatchNote'] = 'Bridge/tunnel name may differ from roadway route; verify manually'
            return out
        match, explanation = address_supports_route(address, street.get('routeName'),
                                                    street.get('unitBlock'))
        if not match:
            out['MatchStatus'] = 'REVIEW_ADDRESS_MISMATCH'
            out['MatchNote'] = explanation
            return out
        offset = street.get('offsetInMeters')
        if offset is None or float(offset) > 15:
            out['MatchStatus'] = 'REVIEW_STREET_OFFSET'
            out['MatchNote'] = f'Street offset missing or greater than 15m; {explanation}'
            return out
        if alley and alley.get('offsetInMeters') is not None:
            a_offset = float(alley['offsetInMeters'])
            if a_offset + 3 < float(offset):
                out['MatchStatus'] = 'REVIEW_ALLEY_CLOSER'
                out['MatchNote'] = f'Alley is >3m closer than street; {explanation}'
                return out
        key = (str(street.get('blockKey')), str(street.get('routeId')))
        if key not in cache:
            cache[key] = class_by_block(session, street.get('blockKey'), street.get('routeId'))
            time.sleep(pause)
        road, class_status = cache[key]
        if road is None:
            out['MatchStatus'] = 'REVIEW_' + class_status
            out['MatchNote'] = explanation
            return out
        code = int(road['FHWAFUNCTIONALCLASS'])
        out.update({'FHWAClass': code, 'FHWAClassName': CLASS_NAMES[code],
                    'MatchStatus': 'MATCHED' if class_status == 'OK' else 'REVIEW_MULTIPLE_SAME_CLASS',
                    'MatchNote': explanation + '; route ID and BLOCKKEY confirmed'})
        return out
    except (requests.RequestException, RuntimeError, ValueError, TypeError) as exc:
        out['MatchStatus'] = 'API_ERROR'
        out['MatchNote'] = str(exc)[:300]
        return out


def write_atomic(frame, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + '.tmp')
    frame.to_csv(temp, index=False, encoding='utf-8-sig')
    os.replace(temp, destination)


def validate_resume(current, previous):
    if len(previous) > len(current):
        raise ValueError('Resume checkpoint contains more rows than current input')
    if not {'OBJECTID', 'TrackingNumber', 'LocationID', 'MatchStatus'}.issubset(previous.columns):
        raise ValueError('Resume checkpoint lacks required columns')
    if not previous['OBJECTID'].reset_index(drop=True).equals(current.iloc[:len(previous)]['OBJECTID'].reset_index(drop=True)):
        raise ValueError('Checkpoint OBJECTID ordering differs from input. Refusing unsafe resume.')
    for col in ('TrackingNumber', 'LocationID'):
        if not previous[col].reset_index(drop=True).equals(current.iloc[:len(previous)][col].reset_index(drop=True)):
            raise ValueError(f'Checkpoint {col} differs from input. Refusing unsafe resume.')
    if previous['MatchStatus'].isna().any() or (previous['MatchStatus'] == 'NOT_PROCESSED').any():
        raise ValueError('Checkpoint has missing or unprocessed statuses')


def make_frame(original, results, complete):
    enriched = pd.concat([original.iloc[:len(results)].reset_index(drop=True),
                          pd.DataFrame(results, columns=RESULT_COLUMNS)], axis=1)
    enriched['FHWAClass'] = pd.to_numeric(enriched['FHWAClass'], errors='coerce').astype('Int64')
    enriched['HighestPermitFHWAClass'] = pd.Series(pd.NA, index=enriched.index, dtype='Int64')
    if complete:
        enriched['HighestPermitFHWAClass'] = (enriched.groupby('TrackingNumber')['FHWAClass']
                                              .transform('min').astype('Int64'))
    return enriched


def save_outputs(original, results, output, review_path, complete):
    enriched = make_frame(original, results, complete)
    assert enriched['OBJECTID'].is_unique, 'Duplicate source OBJECTID'
    write_atomic(enriched, output)
    review = enriched[enriched['MatchStatus'] != 'MATCHED'].copy()
    write_atomic(review, review_path)
    return enriched


def main():
    parser = argparse.ArgumentParser(description='DDOT LRS-to-FHWA enrichment with safe resume')
    parser.add_argument('--input', type=Path, default=ROOT / 'output' / 'permits_extracted.csv')
    parser.add_argument('--output', type=Path, default=None)
    parser.add_argument('--review-output', type=Path, default=None)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--limit', type=int, help='Test with first N rows (default 10)')
    group.add_argument('--all', action='store_true', help='Process every input row')
    parser.add_argument('--resume', action='store_true', help='Continue from existing output checkpoint')
    parser.add_argument('--checkpoint-every', type=int, default=25)
    parser.add_argument('--pause', type=float, default=0.2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    if args.pause < 0 or args.checkpoint_every < 1:
        parser.error('--pause must be >=0 and --checkpoint-every must be >=1')
    if not args.input.exists():
        parser.error(f'Input file not found: {args.input}')
    source = pd.read_csv(args.input, low_memory=False)
    if source.empty or 'OBJECTID' not in source or 'TrackingNumber' not in source:
        parser.error('Input missing rows or required identifiers')
    if source['OBJECTID'].duplicated().any():
        parser.error('Source contains duplicate OBJECTID')
    original = source.copy() if args.all else source.head(args.limit or 10).copy()
    output = args.output or ROOT / 'output' / ('permits_enriched.csv' if args.all else 'permits_enriched_preview.csv')
    review_path = args.review_output or output.with_name(output.stem + '_review.csv')
    if output.resolve() == args.input.resolve():
        parser.error('Output must not overwrite source CSV')
    if review_path.resolve() in (output.resolve(), args.input.resolve()):
        parser.error('Review-output must be a separate file')
    results = []
    if args.resume:
        if not output.exists():
            parser.error(f'--resume requires an existing output checkpoint: {output}')
        checkpoint = pd.read_csv(output, low_memory=False)
        try:
            validate_resume(original, checkpoint)
        except ValueError as exc:
            parser.error(str(exc))
        results = checkpoint[RESULT_COLUMNS].where(pd.notnull(checkpoint[RESULT_COLUMNS]), None).to_dict('records')
        LOG.info('Resumed %d of %d rows', len(results), len(original))
    else:
        LOG.info('Starting new run; previous output will be replaced: %s', output)
    cache, session = {}, session_with_retries()
    for ix in range(len(results), len(original)):
        row = original.iloc[ix]
        result = classify_row(row, session, cache, args.pause)
        results.append(result)
        LOG.info('%d/%d OBJECTID=%s -> %s (FHWA=%s)', ix + 1, len(original),
                 row.get('OBJECTID'), result['MatchStatus'], result['FHWAClass'])
        if len(results) % args.checkpoint_every == 0:
            save_outputs(original, results, output, review_path, complete=False)
            LOG.info('Checkpoint saved at %d rows', len(results))
    is_complete = len(results) == len(original) and args.all
    final = save_outputs(original, results, output, review_path, complete=is_complete)
    assert len(final) == len(original)
    LOG.info('Saved %s (%d rows)', output, len(final))
    LOG.info('Review file: %s (%d rows)', review_path, (final['MatchStatus'] != 'MATCHED').sum())
    LOG.info('Status breakdown:\n%s', final['MatchStatus'].value_counts().to_string())
    if is_complete:
        LOG.info('Full-run permit-level highest classes computed')
    else:
        LOG.info('Preview: HighestPermitFHWAClass intentionally blank')


if __name__ == '__main__':
    main()
