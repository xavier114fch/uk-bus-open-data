import os
import json
import requests
import time
from datetime import datetime
from pypolyline.cutil import encode_coordinates, decode_polyline

_data_dir = 'data/tnds'

def retryRequest(url):
	while True:
		r = requests.get(url)

		if r.status_code == 200:
			return r

		elif r.status_code == 400 or r.status_code == 404:
			raise Exception(r.status_code, url)
			break

		elif r.status_code == 429:
			time.sleep(10)

		else:
			raise Exception(r.status_code, url)

def compareDates(_start, _end) -> bool:
	_today = datetime.today().date()
	_start = datetime.fromisoformat(_start).date() if _start and _start != '' else _today
	_end = datetime.fromisoformat(_end).date() if _end and _end != '' else datetime.max.date()

	return (_today < _start) or (_start <= _today <= _end)

def getSlugs(_data_dir) -> dict:
	_all_slugs = {}
	_total_slugs = 0

	_directories = sorted([_item for _item in os.listdir(_data_dir) if os.path.isdir(os.path.join(_data_dir, _item)) and _item != 'stopPoints'])

	for _directory in _directories:
		print(f'Getting slugs in {_directory} ...')

		# NCSD XMLs are in one level deeper
		_dir = f'{_data_dir}/{_directory}/{_directory}_TXC' if _directory == 'NCSD' else f'{_data_dir}/{_directory}'

		for _file in sorted(os.listdir(_dir)):
			if _file.endswith('.json'):
				with open(os.path.join(_dir, _file), 'r') as f:
					_data = json.load(f)
					_total_slugs = _total_slugs + len(list(_data.keys()))
					_tracks_updated = False
					_notes_updated = False

					for _slug, _services in _data.items():
						_all_slugs.setdefault(_slug, [])

						for _service in _services:
							_routes = _service.get('routes', [])

							for _route in _routes:
								_tracks = _route.get('tracks', None)

								if _tracks == []:
									_route['tracks'] = ''
									_tracks_updated = True
									print(f'{_slug} has converted empty tracks to empty string.')

								elif isinstance(_tracks, list):
									_route['tracks'] = encode_coordinates(_tracks, 6).decode('utf-8')
									_tracks_updated = True
									print(f'{_slug} has converted from coordinates to polyine encoded string.')

							_timetables = _service.get('timetables', {})

							for _j, _journeys in _timetables.items():
								for _journey in _journeys:
									_note = _journey.get('note', [])

									if len(_note) > 0:
										_journey['note'] = [_note[0]]
										_notes_updated = True
										print(f'{_slug} has stripped multiple notes to single note')

							_start_date = _service.get('startDate', None)
							_end_date = _service.get('endDate', None)
							_last_modified = _service.get('lastModified', None)

							if compareDates(_start_date, _end_date):
								_all_slugs[_slug].append({
									'filename': _service.get('filename')[1:],
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

						if len(_all_slugs[_slug]) == 0:
							_all_slugs.pop(_slug, None)

					if _tracks_updated or _notes_updated:
						with open(os.path.join(_dir, _file), 'w') as f:
							f.write(json.dumps(_data, ensure_ascii = False, separators=(',', ':')))

	for _slug, _services in _all_slugs.items():
		_duplicated = 0
		_overlapped = 0
		_total = len(_services)
		_to_be_removed = []

		if _total > 1:
			for _i in range(1, _total):
				_previous_service = _services[_i - 1]
				_previous_start_date = _previous_service.get('startDate')
				_previous_start_date = datetime.fromisoformat(_previous_start_date).date() if _previous_start_date else None
				_previous_end_date = _previous_service.get('endDate')
				_previous_end_date = datetime.fromisoformat(_previous_end_date).date() if _previous_end_date else None
				_previous_last_modified = _previous_service.get('lastModified')
				_previous_last_modified = datetime.fromisoformat(_previous_last_modified).date() if _previous_last_modified else None

				_current_service = _services[_i]
				_current_start_date = _current_service.get('startDate')
				_current_start_date = datetime.fromisoformat(_current_start_date).date() if _current_start_date else None
				_current_end_date = _current_service.get('endDate')
				_current_end_date = datetime.fromisoformat(_current_end_date).date() if _current_end_date else None
				_current_last_modified = _current_service.get('lastModified')
				_current_last_modified = datetime.fromisoformat(_current_last_modified).date() if _current_last_modified else None

				if all((_previous_start_date, _previous_end_date, _previous_last_modified, _current_start_date, _current_end_date, _current_last_modified)):
					if _previous_start_date == _current_start_date and _previous_end_date == _current_end_date and  _previous_last_modified <= _current_last_modified:
						_to_be_removed.append(_i - 1)
						_duplicated = _duplicated + 1

					elif _current_start_date < _previous_end_date:
						_to_be_removed.append(_i - 1)
						_overlapped = _overlapped + 1

		_services = [_service for _i, _service in enumerate(_services) if _i not in _to_be_removed]

		if len(_services) == 0:
			_all_slugs.pop(_slug, None)
			print(f'{_slug} has removed {_duplicated} duplicated and {_overlapped} overlapped services with nothing left.')

		elif _duplicated > 0 or _overlapped > 0:
			print(f'{_slug} has removed {_duplicated} duplicated and {_overlapped} overlapped services out of {_total}.')

		_all_slugs[_slug] = _services

	with open(os.path.join(_data_dir, 'all_slugs.json'), 'w') as f:
		f.write(json.dumps(_all_slugs, ensure_ascii = False, separators=(',', ':')))
		_len = len(_all_slugs)
		print(f'Filtered {_len} over {_total_slugs} slugs.')
	print('=====')

def getStopPoints(_data_dir):
	_all_stops = []

	_directories = sorted([_item for _item in os.listdir(_data_dir) if os.path.isdir(os.path.join(_data_dir, _item)) and _item != 'stopPoints'])

	for _directory in _directories:
		print(f'Getting stops in {_directory} ...')

		# NCSD XMLs are in one level deeper
		_dir = f'{_data_dir}/{_directory}/{_directory}_TXC' if _directory == 'NCSD' else f'{_data_dir}/{_directory}'

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
		f.write(json.dumps(_all_stops, ensure_ascii = False, separators=(',', ':')))
		_len = len(_all_stops)
		print(f'Filtered {_len} stops.')
	print('=====')

def compareStopPoints(_data_dir):
	def openTndsStopPoints() -> bool:
		global _tnds_stop_list
		try:
			with open(os.path.join(f'{_data_dir}','all_stop_points.json'), 'r') as f:
				_tnds_stop_list = json.load(f)

		except BaseException:
			print('Cannot open TNDS all stop point list.')

		else:
			return True

	def openNaptan() -> bool:
		global _naptan_list
		try:
			_response = retryRequest('https://github.com/xavier114fch/uk-bus-open-data/raw/gh-pages/data/naptan/naptan_stop_points_all.json')
			_naptan_list = _response.json()

		except BaseException:
			print('Cannot open Naptan list.')

		else:
			return True

	try:
		openTndsStopPoints() and openNaptan()

	except BaseException:
		pass

	else:
		_common_naptan = set(_tnds_stop_list) & set(_naptan_list)

		_stops_in_tnds = [_k for _k in _tnds_stop_list if _k not in _common_naptan]

		with open(os.path.join(_data_dir, 'stops_tnds_only.json'), 'w') as f:
			f.write(json.dumps(_stops_in_tnds, ensure_ascii = False, separators=(',', ':')))
			print(f'There are {len(_stops_in_tnds)} stop points only appear in TNDS')
			print('=====')

		# print('Stops only in TNDS:')
		# print(list(_stops_in_tnds.keys()))
		# print('===')
		# print('Stops only in Naptan:')
		# print(list(_stops_in_naptan.keys()))

def main():
	getSlugs(_data_dir)
	getStopPoints(_data_dir)
	compareStopPoints(_data_dir)

if __name__ == "__main__":
	main()