import os
import json
import requests
import time
import logging
from datetime import datetime
from pypolyline.cutil import encode_coordinates

# Logger configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

_data_dir = 'data/tnds'

# Session configuration
session = requests.Session()
request_timeout = 30

def retry_request(url: str, *, max_retries: int = 5, backoff_delay: int = 1) -> requests.Response:
    """Return a Response object or exit after repeated failures.

    The function retries on HTTP status 429 (rate‑limited) or any
    :class:`requests.RequestException`.  After the maximum number of
    attempts the process exits with a message.
    """
    for attempt in range(max_retries):
        try:
            resp = session.get(url, timeout=request_timeout)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                logger.warning(f'Rate limited (429). Waiting {backoff_delay}s before retry…')
                time.sleep(backoff_delay)
                backoff_delay *= 2
                continue
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f'Request exception: {exc}. Retrying…')
            time.sleep(backoff_delay)
            backoff_delay *= 2
    raise SystemExit(f'Failed to fetch {url} after {max_retries} attempts.')

def compare_dates(_start, _end) -> bool:
    _today = datetime.today().date()
    _start = datetime.fromisoformat(_start).date() if _start and _start != '' else _today
    _end = datetime.fromisoformat(_end).date() if _end and _end != '' else datetime.max.date()

    return (_today < _start) or (_start <= _today <= _end)

def get_slugs(_data_dir: str) -> None:
    _all_slugs = {}
    _total_slugs = 0

    _directories = sorted([os.path.join(_data_dir, _item) for _item in os.listdir(_data_dir) if os.path.isdir(os.path.join(_data_dir, _item)) and _item != 'stopPoints'])

    for _dir in _directories:
        logger.info(f'Getting slugs in {_dir} ...')

        # NCSD XMLs are in one level deeper
        #_dir = f'{_data_dir}/{_directory}/{_directory}_TXC' if _directory == 'NCSD' else f'{_data_dir}/{_directory}'

        for _file in sorted(os.listdir(_dir)):
            if _file.endswith('.json'):
                with open(os.path.join(_dir, _file), 'r') as f:
                    _data = json.load(f)
                    _total_slugs = _total_slugs + len(list(_data.keys()))
                    _updated = False

                    for _slug, _services in _data.items():
                        _all_slugs.setdefault(_slug, [])

                        # Sort services deterministically by startDate and lastModified for duplicate/overlap logic
                        _services.sort(
                            key=lambda s: (
								datetime.fromisoformat(s.get('startDate') or str(datetime.now().date())),
								datetime.fromisoformat(s.get('lastModified') or str(datetime.now().date()))
                            )
                        )

                        for _service in _services:
                            _routes = _service.get('routes', [])

                            # convert tracks to polyline-encoded string if tracks is a list of coordinates, or convert empty tracks to empty string
                            for _route in _routes:
                                _tracks = _route.get('tracks', None)

                                if _tracks == []:
                                    _route['tracks'] = ''
                                    _updated = True
                                    logger.debug(f'{_slug} has converted empty tracks to empty string.')

                                elif isinstance(_tracks, list):
                                    _route['tracks'] = encode_coordinates(_tracks, 6).decode('utf-8')
                                    _updated = True
                                    logger.debug(f'{_slug} has converted from coordinates to polyine-encoded string.')

                                _direction = _route.get('_direction', [])

                                if len(_direction) > 0:
                                    if 'inbound' in _direction and 'outbound' in _direction:
                                        logger.info(f'{_slug} has both inbound and outbound directions.')

                            _timetables = _service.get('timetables', {})

                            for _j, _journeys in _timetables.items():
                                for _journey in _journeys:
                                    # Convert multiple notes to single note if there are multiple notes, or convert empty notes to empty list
                                    _note = _journey.get('note', [])

                                    if len(_note) > 0:
                                        _journey['note'] = [_note[0]]
                                        _updated = True
                                        logger.debug(f'{_slug} has stripped multiple notes to single note.')

                                    # Convert sequence numbers from string to integers if there are sequence numbers, or convert empty sequence numbers to empty list
                                    _sequences = _journey.get('sequenceNumber', [])

                                    if len(_sequences) > 0:
                                        _journey['sequenceNumber'] = [int(_s) for _s in _sequences if isinstance(_s, str) and _s.isdigit()]
                                        _updated = True
                                        logger.debug(f'{_slug} has changed sequence numbers from string to integers.')

                                    # Convert activities to empty string if there are pick up and set down activities, or convert empty activities to empty list
                                    _activities = _journey.get('activities', [])

                                    if len(_activities) > 0:
                                        _journey['activities'] = ['' for _a in _activities if isinstance(_a, str) and _a == 'pickUpAndSetDown']
                                        _updated = True
                                        logger.debug(f'{_slug} has stripped multiple pickUpAndSetDown.')

                                    _displays = _journey.get('dynamicDestinationDisplay', [])

                                    if len(_displays) > 0 and all(_d == '' for _d in _displays):
                                        _journey['dynamicDestinationDisplay'] = []
                                        _updated = True
                                        logger.debug(f'{_slug} has stripped empty dynamicDestinationDisplay.')

                            _start_date = _service.get('startDate', None)
                            _end_date = _service.get('endDate', None)
                            _last_modified = _service.get('lastModified', None)

                            _filename = _service.get('filename', None)
                            if _filename:
                                if _filename.startswith('_') and _filename.endswith('.json'):
                                    _filename = _filename[1:]

                                if _filename.endswith('.xml'):
                                    _filename = os.path.splitext(_filename)[0] + '.json'

                            if compare_dates(_start_date, _end_date):
                                _all_slugs[_slug].append({
                                    'filename': _filename,
                                    'mode': _service.get('mode'),
                                    'region': _service.get('region'),
                                    'name': _service.get('name'),
                                    'description': _service.get('description'),
                                    'operators': _service.get('operators'),
                                    'lastModified': _service.get('lastModified'),
                                    'publicUse': _service.get('publicUse'),
                                    'startDate': _start_date,
                                    'endDate': _end_date,
                                })

                            else:
                                # Service expired – keep file but drop entry
                                _data = {}
                                _updated = True

                        if len(_all_slugs[_slug]) == 0:
                            _all_slugs.pop(_slug, None)

                    if _updated:
                        with open(os.path.join(_dir, _file), 'w') as f:
                            f.write(json.dumps(_data, ensure_ascii=False, separators=(',', ':'), sort_keys=True))

    for _slug, _services in _all_slugs.items():
        _duplicated = 0
        _overlapped = 0
        _total = len(_services)
        _to_be_removed = []

        if _total > 1:
            _filtered = []
            for svc in _services:
                if not _filtered:
                    _filtered.append(svc)
                    continue

                prev = _filtered[-1]

                # Duplicate handling – keep newer based on lastModified
                if (
                    svc['startDate'] == prev['startDate'] and
                    svc['endDate'] == prev['endDate'] and
                    svc['lastModified'] > prev['lastModified']
                ):
                    _filtered.pop()
                    _filtered.append(svc)
                    logger.info(f"{_slug} duplicate removed, kept newer.")
                    _duplicated += 1
                    continue

                # Overlap handling – keep newer based on lastModified
                current_start = datetime.fromisoformat(svc['startDate'])
                prev_end = datetime.fromisoformat(prev['endDate'])
                if current_start < prev_end:
                    if svc['lastModified'] > prev['lastModified']:
                        _filtered.pop()
                        _filtered.append(svc)
                        logger.info(f"{_slug} overlap resolved, kept newer.")
                        _overlapped += 1
                    else:
                        logger.info(f"{_slug} overlap ignored, earlier kept.")
                    continue

                _filtered.append(svc)

            _services = _filtered

        if len(_services) == 0:
            _all_slugs.pop(_slug, None)
            logger.info(f'{_slug} has removed {_duplicated} duplicated and {_overlapped} overlapped services with nothing left.')

        elif _duplicated > 0 or _overlapped > 0:
            logger.info(f'{_slug} has removed {_duplicated} duplicated and {_overlapped} overlapped services out of {_total}.')

        _all_slugs[_slug] = _services

    with open(os.path.join(_data_dir, 'all_slugs.json'), 'w') as f:
        f.write(json.dumps(_all_slugs, ensure_ascii=False, separators=(',', ':'), sort_keys=True))
        _len = len(_all_slugs)
        logger.info(f'Filtered {_len} over {_total_slugs} slugs.')
    logger.info('=====')


def get_stop_points(_data_dir: str) -> None:
    _all_stops = []

    _directories = sorted([os.path.join(_data_dir, _item) for _item in os.listdir(_data_dir) if os.path.isdir(os.path.join(_data_dir, _item)) and _item != 'stopPoints'])

    for _dir in _directories:
        logger.info(f'Getting stops in {_dir} ...')

        # NCSD XMLs are in one level deeper
        # _dir = f'{_data_dir}/{_directory}/{_directory}_TXC' if _directory == 'NCSD' else f'{_data_dir}/{_directory}'

        for _file in sorted(os.listdir(_dir)):
            if _file.endswith('.json'):
                with open(os.path.join(_dir, _file), 'r') as f:
                    _data = json.load(f)

                    for _slug, _services in _data.items():
                        for _service in _services:
                            _routes = _service.get('routes', {})

                            for _route in _routes:
                                _stop_points = _route.get('stopPoints', [])
                                _all_stops.extend(_stop_points)

    _all_stops = list(set(_all_stops))

    with open(os.path.join(_data_dir, 'all_stop_points.json'), 'w') as f:
        f.write(json.dumps(_all_stops, ensure_ascii=False, separators=(',', ':'), sort_keys=True))
        _len = len(_all_stops)
        logger.info(f'Filtered {_len} stops.')
    logger.info('=====')


def compare_stop_points(_data_dir: str) -> None:
    def open_TNDS_stop_points() -> bool:
        global _tnds_stop_list
        try:
            with open(os.path.join(f'{_data_dir}','all_stop_points.json'), 'r') as f:
                _tnds_stop_list = json.load(f)

        except BaseException:
            logger.info('Cannot open TNDS all stop point list.')
            return False

        else:
            return True

    def open_naptan() -> bool:
        global _naptan_list
        try:
            _response = retry_request('https://github.com/xavier114fch/uk-bus-open-data/raw/gh-pages/data/naptan/naptan_stop_points_all.json')
            _naptan_list = _response.json()

        except BaseException:
            logger.info('Cannot open Naptan list.')
            return False

        else:
            return True

    try:
        open_TNDS_stop_points() and open_naptan()

    except BaseException:
        pass

    else:
        _common_naptan = set(_tnds_stop_list) & set(_naptan_list)

        _stops_in_tnds = [_k for _k in _tnds_stop_list if _k not in _common_naptan]

        with open(os.path.join(_data_dir, 'stops_tnds_only.json'), 'w') as f:
            f.write(json.dumps(_stops_in_tnds, ensure_ascii=False, separators=(',', ':'), sort_keys=True))
            logger.info(f'There are {len(_stops_in_tnds)} stop points only appear in TNDS')
            logger.info('=====')

    # logger.info('Stops only in TNDS:')
    # logger.info(list(_stops_in_tnds.keys()))
    # logger.info('===')
    # logger.info('Stops only in Naptan:')
    # logger.info(list(_naptan_list))


def main():
    get_slugs(_data_dir)
    get_stop_points(_data_dir)
    compare_stop_points(_data_dir)


if __name__ == "__main__":
    main()
