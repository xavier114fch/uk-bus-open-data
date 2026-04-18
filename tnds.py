import os
import zipfile
import json
import xmltodict
import re
import requests
import time
import logging
from ftplib import FTP, error_temp, all_errors
from datetime import datetime
from tenacity import retry, wait_fixed, stop_after_attempt, retry_if_exception_type
from pypolyline.cutil import encode_coordinates

# Logger configuration
logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s [%(levelname)s] %(message)s',
	datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Data and URL configuration
_data_dir = 'data/tnds'

# Session configuration
session = requests.Session()
request_timeout = 30

# Safe chars configuration
_safe_chars = re.compile(r'[^a-zA-Z0-9\-\+\.\|]')

_all_stops = {}

# Helper: establish connection
def get_ftp_session(host: str, user: str, pwd: str) -> FTP:
	ftp = FTP(host, timeout=120)
	ftp.sock.settimeout(120)
	ftp.login(user, pwd)
	ftp.set_pasv(True)
	logger.info(f"FTP connected to {host}")
	return ftp

# Helper: keep-alive / reconnect
def ftp_alive_or_reconnect(ftp: FTP, host: str, user: str, pwd: str) -> FTP:
	if ftp.sock is None:
		ftp = get_ftp_session(host, user, pwd)
		return ftp
	try:
		ftp.voidcmd("NOOP")
		logger.debug("FTP session is alive.")
		return ftp
	except all_errors as exc:
		logger.warning(f"FTP session dropped – reconnecting: {exc}")
		try:
			ftp.quit()
		except Exception:
			pass
		ftp = get_ftp_session(host, user, pwd)
		logger.info("Reconnected to FTP.")
		return ftp

# Helper: download with retries
def download_file(ftp: FTP, host: str, user: str, pwd: str, remote_path: str, local_path: str, max_retries: int = 10, backoff_delay: int = 2):
	for attempt in range(max_retries):
		try:
			with open(local_path, "wb") as fh:
				ftp = ftp_alive_or_reconnect(ftp, host, user, pwd)
				ftp.retrbinary(f"RETR {remote_path}", fh.write)
			logger.info(f"Downloaded {remote_path} → {local_path}")
			return
		except Exception as exc:
			logger.warning(f"Attempt {attempt} failed for {remote_path}: {exc}")
			ftp = ftp_alive_or_reconnect(ftp, host, user, pwd)
			time.sleep(backoff_delay)
			backoff_delay *= 2
	raise SystemExit(f'Failed to download {remote_path} after {max_retries} attempts')

# Helper: batch zip extraction
def extract_all_zips(data_dir: str):
	for root, _dirs, files in os.walk(data_dir):
		for f in files:
			if f.lower().endswith(".zip"):
				zip_path = os.path.join(root, f)
				extract_dir = os.path.join(root, os.path.splitext(f)[0])
				os.makedirs(extract_dir, exist_ok=True)
				with zipfile.ZipFile(zip_path, "r") as zip_ref:
					zip_ref.extractall(extract_dir)
				logger.info(f"Extracted {zip_path} → {extract_dir}")
				# optional: delete zip after extraction
				# os.remove(zip_path)

	logger.info('=====')

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

@retry(
	wait = wait_fixed(5),
	stop = stop_after_attempt(10),
	retry = retry_if_exception_type((ConnectionResetError, error_temp))
)
def fetch_tnds_data(_data_dir: str) -> None:
	# FTP server details
	_ftp_host = 'ftp.tnds.basemap.co.uk'
	_ftp_user = os.environ.get('TNDS_FTP_USER')
	_ftp_pwd = os.environ.get('TNDS_FTP_PWD')

	if not _ftp_user or not _ftp_pwd:
		raise RuntimeError('Missing FTP credentials from env variables.')

	# Initial connection
	_ftp = get_ftp_session(_ftp_host, _ftp_user, _ftp_pwd)

	# Navigate to the target directory
	_ftp.cwd('/TNDSV2.5')

	# List files
	_file_list = _ftp.nlst()

	for _file_name in _file_list:
		# Keep connection alive / reconnect if needed
		_ftp = ftp_alive_or_reconnect(_ftp, _ftp_host, _ftp_user, _ftp_pwd)

		# Check remote timestamp
		_response = _ftp.sendcmd(f'MDTM {_file_name}')
		_remote_ts = datetime.strptime(_response[4:], '%Y%m%d%H%M%S')

		_local_file_path = f'{_data_dir}/{_file_name}'
		_local_ts = datetime.fromtimestamp(os.path.getmtime(_local_file_path)) if os.path.exists(_local_file_path) else None

		if _local_ts is None or _remote_ts > _local_ts:
			logger.info(f'Getting {_file_name} from TNDS FTP ...')
			os.makedirs(_data_dir, exist_ok=True)
			download_file(_ftp, _ftp_host, _ftp_user, _ftp_pwd, _file_name, _local_file_path)

	if ftp_alive_or_reconnect(_ftp, _ftp_host, _ftp_user, _ftp_pwd):
		_ftp.quit()
		logger.info('FTP connection closed.')

	logger.info('=====')

	# No in‑loop extraction – handled by extract_all_zips after download
	# After processing all files, extract all zip archives
	extract_all_zips(_data_dir)

def convert_tnds(_data_dir: str) -> None:
	_directories = sorted([os.path.join(_data_dir, _item) for _item in os.listdir(_data_dir) if os.path.isdir(os.path.join(_data_dir, _item)) and _item != 'stopPoints'])

	for _directory in _directories:
		logger.info(f'Extracting routes and stops from TNDS XML files in {_directory} ...')

		# NCSD XMLs are in one level deeper
		# _dir = f'{_data_dir}/{_directory}/{_directory}_TXC' if _directory == 'NCSD' else f'{_data_dir}/{_directory}'

		_total_count = 0

		for _file in sorted(os.listdir(_directory)):
			if _file.endswith('.xml'):
				# print(f'Converting TNDS XML file {_dir}/{_file} ...')
				_total_count = _total_count + 1

				with open(os.path.join(_directory, _file), 'r') as f:
					_content = f.read()
					_data = xmltodict.parse(_content, attr_prefix='')
					extract_routes(_directory, _file, _data)
					extract_stops(_directory, _file, _data)

				# with open(os.path.join(_dir, f'_{os.path.splitext(_file)[0]}.json'), 'w', encoding='utf-8') as f:
				# 	json.dump(_data, f, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
							
		logger.info(f'Processed {_total_count} files.')

def compare_dicts(_x: dict, _y: dict) -> bool:
	if sorted(_x.keys()) != sorted(_y.keys()):
		return False

	for _key in _x:
		if _x[_key] != _y[_key]:
			return False

	return True

def compare_dates(_start, _end) -> bool:
	_today = datetime.today().date()
	_start = datetime.fromisoformat(_start).date() if _start and _start != '' else None
	_end = datetime.fromisoformat(_end).date() if _end and _end != '' else None

	return (_start and _today < _start) or (_start and _end and _start <= _today <= _end) or (_start and not _end and _today >= _start)

# Utility to normalise value to a list – accepts an element or a list
def as_list(value: list | str) -> list:
    return value if isinstance(value, list) else [value]

def create_slug(_line_names: list, _origin: str, _dest: str) -> str:
	_line_name_list = '+'.join(_line_names)
	_slug = (f'{_line_name_list}-{_origin.replace(' / ', ' ').replace(' ', '-')}-{_dest.replace(' / ', ' ').replace(' ', '-')}').lower()
	_slug = _safe_chars.sub('', _slug)
	return _slug

def extract_routes(_directory: str, _file: str, _data: dict) -> None:
	# Placeholder for the actual implementation of outputTnds_new
	# This function would take the processed TNDS data and generate the desired output format

	# NCSD XMLs are in one level deeper
	# _dir = f'{_data_dir}/{_directory}/{_directory}_TXC' if _directory == 'NCSD' else f'{_data_dir}/{_directory}'

	_data = _data.get('TransXChange', {})
	_last_modified = _data.get('ModificationDateTime', '')
	_services = _data.get('Services', {}).get('Service', {})

	if not _services:
		logger.warning(f'{_directory}/{_file} has no services.')

	# elif isinstance(_services, list):
	# 	print(f'{_directory}/{_file} has mulitple services.')

	_services = as_list(_services)

	_single_service = {}

	for _service in _services:
		_start_date = _service.get('OperatingPeriod', {}).get('StartDate', '')
		_end_date = _service.get('OperatingPeriod', {}).get('EndDate', '')

		if not compare_dates(_start_date, _end_date):
			continue

		_line_names, _line_ids, _vias, _noc, _routes = [], [], [], [], []
		_journey_patterns, _serviced_organisations, _vehicles = {}, {}, {}

		_line_list = _service.get('Lines', {}).get('Line', [])

		if not _line_list:
			logger.warning(f'{_directory}/{_file} has no lines.')

		# elif isinstance(_line_list, list):
		# 	print(f'{_directory}/{_file} has mulitple lines.')

		_line_list = as_list(_line_list)

		for _line in _line_list:
			_line_names.append(_line.get('LineName', ''))
			_line_ids.append(_line.get('id', ''))

		_mode = _service.get('Mode', 'bus')
		_desc = _service.get('Description', '')
		_public_use = _service.get('PublicUse', False)
		_standard_service = _service.get('StandardService', {})
		_origin = _standard_service.get('Origin', '')
		_dest = _standard_service.get('Destination', '')
		_vias_list = _standard_service.get('Vias', {}).get('Via', [])

		if _desc == '' and _origin != '' and _dest != '':
			_desc = f'{_origin} - {_dest}'

		_vias = as_list(_vias)
		_journey_pattern_list = _standard_service.get('JourneyPattern', [])
		_journey_pattern_list = as_list(_journey_pattern_list)

		for _jp in _journey_pattern_list:
			_journey_pattern = {}

			_jp_key = _jp.get('RouteRef', None)

			if not _jp_key:
				_jp_key = _jp.get('id', None)

			if _jp_key:
				_journey_patterns.setdefault(_jp_key, [])

			else:
				logger.warning(f'{_directory}/{_file}: Empty journey pattern key.')

			# _jp_route_ref = _jp.get('RouteRef')
			# _jp_id = _jp.get('id')

			# if _jp_route_ref and _jp_route_ref not in _journey_patterns:
			# 	_journey_patterns[_jp_route_ref] = []

			# elif _jp_id and _jp_id not in _journey_patterns:
			# 	_journey_patterns[_jp_id] = []

			# if 'id' in _jp:
			# 	_journey_pattern['journeyPatternId'] = _jp['id']

			_journey_pattern.setdefault('destinationDisplay', _jp.get('DestinationDisplay', ''))
			_journey_pattern.setdefault('direction', _jp.get('Direction', ''))

			# if 'JourneyPatternSectionRefs' in _jp:
			# 	_journey_pattern['journeyPatternSectionRefs'] = _jp['JourneyPatternSectionRefs']

			_journey_pattern_section_list = _data.get('JourneyPatternSections', {}).get('JourneyPatternSection', [])
			_journey_pattern_section_list = as_list(_journey_pattern_section_list)
			_journey_pattern_section_ref_list = _jp.get('JourneyPatternSectionRefs', [])
			_journey_pattern_section_ref_list = as_list(_journey_pattern_section_ref_list)

			_jptl_ids = []
			_jptl_routeLinks = []
			_jptl_stops = []
			_runtimes = []
			_activities = []
			_wait_times = []
			_timing_statuses = []
			_sequences = []
			_display = []

			for _i, _jpsf in enumerate(_journey_pattern_section_ref_list):
				for _jps in _journey_pattern_section_list:
					# if _file == '_NE_01_WMS_102_1.json':
					# 	print(f'{_file} {_jpsf} {_jps['id']}')

					if _jps.get('id') == _jpsf:
						_journey_pattern_timing_link_list = _jps.get('JourneyPatternTimingLink', [])
						_journey_pattern_timing_link_list = as_list(_journey_pattern_timing_link_list)

						for _j, _jptl in enumerate(_journey_pattern_timing_link_list):
							_jptl_id = _jptl.get('id')
							_jptl_routeLinkRef = _jptl.get('RouteLinkRef')
							_from_stop_point = _jptl.get('From', {}).get('StopPointRef')
							_to_stop_point = _jptl.get('To', {}).get('StopPointRef')
							_jptl_runtime = _jptl.get('RunTime')

							# if _from_stop_point and _to_stop_point and _from_stop_point == _to_stop_point and _jptl_runtime in ['PT0S', 'PT0M0S', 'PT0H0M0S']:
							# 	print(f'{_directory}/{_file}: 0s journey for same from/to stop points in JourneyPatternTimingLink id {_jptl_id}.')
							# 	continue

							_jptl_ids.append(_jptl_id)
							_jptl_routeLinks.append(_jptl_routeLinkRef)
							_runtimes.append(_jptl_runtime)

							if _i == 0 and _j == 0:
								_jptl_stops.append(_from_stop_point)
								_activities.append(_jptl.get('From', {}).get('Activity', 'pickUp'))
								_wait_times.append(_jptl.get('From', {}).get('WaitTime', ''))
								_timing_statuses.append(_jptl.get('From', {}).get('TimingStatus', ''))
								_sequences.append(_jptl.get('From', {}).get('SequenceNumber', ''))
								_display.append(_jptl.get('From', {}).get('DynamicDestinationDisplay', ''))

							if _i == len(_journey_pattern_timing_link_list) - 1:
								_activities.append('setDown')

							else:
								_activities.append(_jptl.get('To', {}).get('Activity', ''))

							_jptl_stops.append(_to_stop_point)
							_timing_statuses.append(_jptl.get('To', {}).get('TimingStatus', ''))
							_wait_times.append(_jptl.get('To', {}).get('WaitTime', ''))
							_sequences.append(_jptl.get('To', {}).get('SequenceNumber', ''))
							_display.append(_jptl.get('To', {}).get('DynamicDestinationDisplay', ''))

			# _journey_pattern['journeyPatternTimingLinkIds'] = _jptl_ids
			_journey_pattern.setdefault('routeLinkId', _jptl_routeLinks)
			_journey_pattern.setdefault('stopPoints', _jptl_stops)
			_journey_pattern.setdefault('runtimes', _runtimes)
			_journey_pattern.setdefault('activities', _activities)
			_journey_pattern.setdefault('waitTimes', _wait_times)
			_journey_pattern.setdefault('timingStatuses', _timing_statuses)
			_journey_pattern.setdefault('sequenceNumber', [int(_s) for _s in _sequences if isinstance(_s, str) and _s.isdigit()])
			_journey_pattern.setdefault('dynamicDestinationDisplay', _display)

			_vehicle_journey_list = _data.get('VehicleJourneys', {}).get('VehicleJourney', [])
			_vehicle_journey_list = as_list(_vehicle_journey_list)

			_departures = []
			for _vj in _vehicle_journey_list:
				_journey_pattern.setdefault('lineId', _vj.get('LineRef', ''))
				_notes = _vj.get('Note', [])
				_journey_pattern_notes = []

				_notes = as_list(_notes)

				for _note in _notes:
					_journey_pattern_notes.append(_note.get('NoteText', ''))

				_journey_pattern.setdefault('note', _journey_pattern_notes)

				_operating_profile = {}

				_days_of_week = _vj.get('OperatingProfile', {}).get('RegularDayType', {}).get('DaysOfWeek', {}) or _service.get('OperatingProfile', {}).get('RegularDayType', {}).get('DaysOfWeek', {})

				if _days_of_week:
					_operating_profile.setdefault('regular', list(_days_of_week.keys()))

				_days_of_operation_list = (_vj.get('OperatingProfile', {}).get('SpecialDaysOperation', {}) or {}).get('DaysOfOperation', {}).get('DateRange', []) or (_service.get('OperatingProfile', {}).get('SpecialDaysOperation', {}) or {}).get('DaysOfOperation', {}).get('DateRange', [])

				if _days_of_operation_list:
					_special = _operating_profile.setdefault('special', {})
					_running = _special.setdefault('running', [])

					_days_of_operation_list = as_list(_days_of_operation_list)

					for _days_of_operation in _days_of_operation_list:
						_running.append({
							'startDate': _days_of_operation.get('StartDate', ''),
							'endDate': _days_of_operation.get('EndDate', ''),
							'note': _days_of_operation.get('Note', '')
						})

				_days_of_non_operation_list = (_vj.get('OperatingProfile', {}).get('SpecialDaysOperation', {}) or {}).get('DaysOfNonOperation', {}).get('DateRange', []) or (_service.get('OperatingProfile', {}).get('SpecialDaysOperation', {}) or {}).get('DaysOfNonOperation', {}).get('DateRange', [])

				if _days_of_non_operation_list:
					_special = _operating_profile.setdefault('special', {})
					_not_running = _special.setdefault('notRunning', [])

					_days_of_non_operation_list = as_list(_days_of_non_operation_list)

					for _days_of_non_operation in _days_of_non_operation_list:
						_not_running.append({
							'startDate': _days_of_non_operation.get('StartDate', ''),
							'endDate': _days_of_non_operation.get('EndDate', ''),
							'note': _days_of_non_operation.get('Note', '')
						})

				_days_of_operation = ((_vj.get('OperatingProfile') or {}).get('BankHolidayOperation') or {}).get('DaysOfOperation', {}) or _service.get('OperatingProfile', {}).get('BankHolidayOperation', {}).get('DaysOfOperation', {})

				if _days_of_operation:
					_holidays = _operating_profile.setdefault('holidays', {})
					_running = _holidays.setdefault('running', [])

					_running.extend(list(_days_of_operation.keys()))
					
					_other_public_holidays = []
					_other_public_holiday_list = _days_of_operation.get('OtherPublicHoliday', [])

					_other_public_holiday_list = as_list(_other_public_holiday_list)

					for _other_public_holiday in _other_public_holiday_list:
						_other_public_holidays.append({
							'description': _other_public_holiday.get('Description', ''),
							'date': _other_public_holiday.get('Date', '')
						})

					_running.extend(_other_public_holidays)

				_days_of_non_operation = ((_vj.get('OperatingProfile') or {}).get('BankHolidayOperation') or {}).get('DaysOfNonOperation', {}) or _service.get('OperatingProfile', {}).get('BankHolidayOperation', {}).get('DaysOfNonOperation', {})

				if _days_of_non_operation:
					_holidays = _operating_profile.setdefault('holidays', {})
					_not_running = _holidays.setdefault('notRunning', [])

					_not_running.extend(list(_days_of_non_operation.keys()))

					_other_public_holiday_list = _days_of_non_operation.get('OtherPublicHoliday', [])
					
					_other_public_holiday_list = as_list(_other_public_holiday_list)

					_other_public_holidays = []

					for _other_public_holiday in _other_public_holiday_list:
						_other_public_holidays.append({
							'description': _other_public_holiday.get('Description', ''),
							'date': _other_public_holiday.get('Date', '')
						})

					_not_running.extend(_other_public_holidays)

				_days_of_operation = _vj.get('OperatingProfile', {}).get('ServicedOrganisationDayType', {}).get('DaysOfOperation', []) or _service.get('OperatingProfile', {}).get('ServicedOrganisationDayType', {}).get('DaysOfOperation', [])

				if _days_of_operation:
					_serviced_orgs = _operating_profile.setdefault('servicedOrganisations', {})
					_running = _serviced_orgs.setdefault('running', [])

					_days_of_operation = as_list(_days_of_operation)

					_running.extend(_days_of_operation)

				_days_of_non_operation = _vj.get('OperatingProfile', {}).get('ServicedOrganisationDayType', {}).get('DaysOfNonOperation', []) or _service.get('OperatingProfile', {}).get('ServicedOrganisationDayType', {}).get('DaysOfNonOperation', [])

				if _days_of_non_operation:
					_serviced_orgs = _operating_profile.setdefault('servicedOrganisations', {})
					_not_running = _serviced_orgs.setdefault('notRunning', [])
					_days_of_non_operation = as_list(_days_of_non_operation)
					_not_running.extend(_days_of_non_operation)

				_vehicle_journey_ref = _vj.get('VehicleJourneyRef')
				_jp_id = _jp.get('id')
				
				if _vehicle_journey_ref:
					for _vk in _vehicle_journey_list:
						_vehicle_journey_code = _vk.get('VehicleJourneyCode')
						_journey_pattern_ref = _vk.get('JourneyPatternRef')
						_departure_time = _vk.get('DepartureTime')
						_departure_day_shift = int(_vk.get('DepartureDayShift', '0'))

						# if _departure_day_shift == 1:
						# 	print(_file, _vehicle_journey_code, _journey_pattern_ref, _departure_time, _departure_day_shift)

						if _vehicle_journey_ref == _vehicle_journey_code and _journey_pattern_ref == _jp_id and _departure_time:
							for _departure in _departures:
								if compare_dicts(_departure.get('profiles'), _operating_profile) and _departure_time not in _departure.setdefault('departures', []):
									_departure.setdefault('departures', []).append(_departure_time)
									_departure.setdefault('dayShift', []).append(_departure_day_shift)
									break

							else:
								_departures.append({
									# 'codes': [_vehicle_journey_code],
									'profiles': _operating_profile,
									'departures': [_departure_time],
									'dayShift': [_departure_day_shift]
								})

				_journey_pattern_ref = _vj.get('JourneyPatternRef')
				_departure_time = _vj.get('DepartureTime')
				_departure_day_shift = int(_vj.get('DepartureDayShift', '0'))

				# if _departure_day_shift == 1:
				# 	print(_file, _journey_pattern_ref, _departure_time, _departure_day_shift)

				if _journey_pattern_ref and _journey_pattern_ref == _jp_id and _departure_time:
					for _departure in _departures:
						if compare_dicts(_departure.get('profiles'), _operating_profile) and _departure_time not in _departure.setdefault('departures', []):
							_departure.setdefault('departures', []).append(_departure_time)
							_departure.setdefault('dayShift', []).append(_departure_day_shift)
							break

					else:
						_departures.append({
							'profiles': _operating_profile,
							'departures': [_departure_time],
							'dayShift': [_departure_day_shift]
						})

				_vjtl_list = None

				_journey_pattern_ref = _vj.get('JourneyPatternRef', '')
				_vjtl_list = _vj.get('VehicleJourneyTimingLink', []) if _journey_pattern_ref else None
				_vehicle_journey_ref = _vj.get('VehicleJourneyRef', '')

				if _vehicle_journey_ref:
					for _vk in _vehicle_journey_list:
						_vehicle_journey_code = _vk.get('VehicleJourneyCode', '')
						_vehicle_journey_timing_link = _vk.get('VehicleJourneyTimingLink', '')

						if _vehicle_journey_ref == _vehicle_journey_code and _vehicle_journey_timing_link:
							_vjtl_list = _vehicle_journey_timing_link

							break

				if _vjtl_list:
					_vjtl_list = as_list(_vjtl_list)

					for _vjtl in _vjtl_list:
						_vtjl_jptlr = _vjtl.get('JourneyPatternTimingLinkRef', {})

						if _vtjl_jptlr and _vtjl_jptlr in _jptl_ids:
							_index = int(_jptl_ids.index(_vtjl_jptlr))

							_from = _vjtl.get('From', {})
							_from_activity = None
							_from_wait_time = ''

							if _from:
								_from_activity = _vjtl.get('From', {}).get('Activity', {})
								_from_wait_time = _vjtl.get('From', {}).get('WaitTime', '')

							_to = _vjtl.get('To', {})
							_to_activity = None
							_to_wait_time = ''
							
							if _to:
								_to_activity = _vjtl.get('To', {}).get('Activity', {})
								_to_wait_time = _vjtl.get('To', {}).get('WaitTime', '')

							_activities = _journey_pattern.get('activities', {})
							_wait_times = _journey_pattern.get('waitTimes', {})

							if _index == 0 and _from_activity:
								_activities[_index] = _from_activity
								_wait_times[_index] = _from_wait_time

							if _to_activity:
								_activities[_index + 1] = _to_activity
								_wait_times[_index + 1] = _to_wait_time

				_vehicle_code = _vj.get('Operational', {}).get('VehicleType', {}).get('VehicleTypeCode', '')
				_vehicle_description = _vj.get('Operational', {}).get('VehicleType', {}).get('Description', '')

				_vc = _vehicles.setdefault(_vehicle_code, {})
				_vc.setdefault('description', _vehicle_description)

				_journey_pattern.setdefault('vehicle', _vehicle_code)

			# for _departure in _departures:
			# 	_departure['departures'] = sorted(list(set(_departure['departures'])))

			_journey_pattern.setdefault('schedules', _departures)
			_journey_patterns[_jp_key].append(_journey_pattern)

		_operators_list = _data.get('Operators', {}).get('Operator', [])
		_operators_list = as_list(_operators_list)

		for _operator in _operators_list:
			_noc.append(_operator.get('NationalOperatorCode', _operator.get('OperatorCode', '')))

		_route_list = _data.get('Routes', {}).get('Route', [])
		_journey_pattern_list = _service.get('StandardService', {}).get('JourneyPattern', [])

		if _route_list:
			_route_list = as_list(_route_list)

			for _route in _route_list:
				_route_id = _route.get('id', '')
				_route_section_ref = _route.get('RouteSectionRef', [])
				_route_link_ids, _stop_points, _distance, _tracks, _direction = [], [], [], [], []

				_route_section_ref = as_list(_route_section_ref)
				_route_sections = _data.get('RouteSections', {}).get('RouteSection', [])
				_route_sections = as_list(_route_sections)

				_links = []
				for _ref in _route_section_ref:
					for _route_section in _route_sections:
						if _ref == _route_section.get('id'):
							_route_link = _route_section.get('RouteLink', [])

							if not _route_link:
								print(f'{_directory}/{_file}: Missing RouteLink')

							elif not isinstance(_route_link, list):
								_links.append(_route_link)

							else:
								_links.extend(_route_link)

				for _link in _links:
					_route_link_ids.append(_link.get('id', ''))
					_stop_points.append(_link.get('From', {}).get('StopPointRef', ''))
					_d = _link.get('Distance')
					_distance.append(int(_d) if _d is not None else None)
					_direction.append(_link.get('Direction', ''))

					_track_locations = _link.get('Track', {}).get('Mapping', {}).get('Location', [])

					if not _track_locations:
						# print(f'{_directory}/{_file}: Missing Track')
						continue

					for _track_location in _track_locations:
						_translation = _track_location.get('Translation', {})

						if _translation:
							_lon = _translation.get('Longitude', None)
							_lat = _translation.get('Latitude', None)

						else:
							_lon = _track_location.get('Longitude', None)
							_lat = _track_location.get('Latitude', None)
							
						if _lon and _lat:
							_tracks.append([float(_lon), float(_lat)])

				if _tracks == []:
					_tracks = ''

				else:
					_tracks = encode_coordinates(_tracks, 6).decode('utf-8')

				_link = _links[-1]
				_stop_points.append(_link.get('To', {}).get('StopPointRef', ''))

				_routes.append({
					'routeId': _route_id,
					'routeLinkIds': _route_link_ids,
					# 'sectionRef': _route_section_ref,
					'description': _route.get('Description', ''),
					'stopPoints' : _stop_points,
					'distance': _distance,
					'tracks': _tracks,
					'direction': _direction
				})

		elif _journey_pattern_list:
			# print(f'{_directory}/{_file} does not have <Route>.')

			if not isinstance(_journey_pattern_list, list):
				_journey_pattern_list = [_journey_pattern_list]

			for _journey_pattern in _journey_pattern_list:
				_stop_points, _distance, _tracks, _direction = [], [], [], []
				_journey_pattern_section_ref_list = _journey_pattern.get('JourneyPatternSectionRefs', [])

				if not isinstance(_journey_pattern_section_ref_list, list):
					_journey_pattern_section_ref_list = [_journey_pattern_section_ref_list]

				_journey_pattern_section_list = _data.get('JourneyPatternSections', {}).get('JourneyPatternSection', [])

				if not isinstance(_journey_pattern_section_list, list):
					_journey_pattern_section_list = [_journey_pattern_section_list]

				_links = []
				for _journey_pattern_section_ref in _journey_pattern_section_ref_list:
					for _journey_pattern_section in _journey_pattern_section_list:
						if _journey_pattern_section_ref == _journey_pattern_section.get('id', ''):
							_journey_pattern_timing_link = _journey_pattern_section.get('JourneyPatternTimingLink', [])

							if not isinstance(_journey_pattern_timing_link, list):
								_links.append(_journey_pattern_timing_link)

							else:
								_links.extend(_journey_pattern_timing_link)

				for _link in _links:
					_stop_points.append(_link.get('From', {}).get('StopPointRef', ''))
					_d = _link.get('Distance')
					_distance.append(int(_d) if _d is not None else None)
					_direction.append(_journey_pattern.get('Direction', ''))

					_track_locations = _link.get('Track', {}).get('Mapping', {}).get('Location', [])

					if not _track_locations:
						# print(f'{_directory}/{_file}: Missing Track')
						continue

					for _track_location in _track_locations:
						_translation = _track_location.get('Translation', {})

						if _translation:
							_lon = _translation.get('Longitude', None)
							_lat = _translation.get('Latitude', None)

						else:
							_lon = _track_location.get('Longitude', None)
							_lat = _track_location.get('Latitude', None)
							
						if _lon and _lat:
							_tracks.append([float(_lon), float(_lat)])

				if _tracks == []:
					_tracks = ''

				else:
					_tracks = encode_coordinates(_tracks, 6).decode('utf-8')

				_link = _links[-1]
				_stop_points.append(_link.get('To', {}).get('StopPointRef', ''))

				_routes.append({
					'routeId': _journey_pattern.get('RouteRef', ''),
					# 'sectionRef': _route_section_ref,
					'description': _desc,
					'stopPoints' : _stop_points,
					'distance': _distance,
					'tracks': _tracks,
					'direction': _direction
				})

		_serviced_org_list = _data.get('ServicedOrganisations', {}).get('ServicedOrganisation', [])

		if not isinstance(_serviced_org_list, list):
			_serviced_org_list = [_serviced_org_list]

		for _serviced_org in _serviced_org_list:
			_org_code = _serviced_organisations.setdefault(_serviced_org.get('OrganisationCode', ''), {})
			_org_code.setdefault('name', _serviced_org.get('Name', ''))

			_holidays = []
			_holiday_list = _serviced_org.get('Holidays', {}).get('DateRange', [])
			_holiday_list = as_list(_holiday_list)

			for _holiday in _holiday_list:
				_holidays.append({
					'startDate': _holiday.get('StartDate', ''),
					'endDate': _holiday.get('EndDate', ''),
					'description': _holiday.get('Description', '')
				})

			if _holidays:
				_org_code.setdefault('holidays', _holidays)

			_working_days = []
			_working_day_list = _serviced_org.get('WorkingDays', {}).get('DateRange', [])
			_working_day_list = as_list(_working_day_list)

			for _working_day in _working_day_list:
				_working_days.append({
					'startDate': _working_day.get('StartDate', ''),
					'endDate': _working_day.get('EndDate', ''),
					'description': _working_day.get('Description', '')
				})

			if _working_days:
				_org_code.setdefault('workingDays', _working_days)

		_slug = create_slug(_line_names, _origin, _dest)
		# print(f'{_directory}/{_file}: [{_mode}][{_noc}] {_slug}')

		_single_service.setdefault(_slug, [])
		_single_service[_slug].append({
			'filename': _file,
			'mode': _mode,
			'region': _directory.split('/')[2],
			'lineId': _line_ids,
			'name': _line_names,
			'origin': _origin,
			'destination': _dest,
			'vias': _vias,
			'description': _desc,
			'operators': _noc,
			'lastModified': _last_modified,
			'publicUse': _public_use,
			'startDate': _start_date,
			'endDate': _end_date,
			'routes': _routes,
			'timetables': dict(sorted(_journey_patterns.items())),
			'vehicles': _vehicles,
			'servicedOrganisations': _serviced_organisations
		})

		# _all_slugs.setdefault(_slug, [])
		# _all_slugs[_slug].append({
		# 	'filename': _file[1:],
		# 	'mode': _mode,
		# 	'region': _directory,
		# 	'name': _line_names,
		# 	'description': _desc,
		# 	'operators': _noc,
		# 	'lastModified': _last_modified,
		# 	'publicUse': _public_use,
		# 	'startDate': _start_date,
		# 	'endDate': _end_date,
		# })

	if _single_service != {}:
		with open(os.path.join(_directory, f'{os.path.splitext(_file)[0]}.json'), 'w') as f:
			f.write(json.dumps(_single_service, ensure_ascii = False, separators=(',', ':'), sort_keys=True))

def extract_stops(_directory: str, _file: str, _data: dict) -> None:
	_stop_points = _data.get('TransXChange', {}).get('StopPoints', {})
	_atco_code, _name, _locality_ref, _locality_name, _origin, _dest, _slug = '', '', '', '', '', '', ''
	_line_names = []

	_services = _data.get('TransXChange', {}).get('Services', {}).get('Service', {})

	_services = as_list(_services)

	for _service in _services:
		_line_list = _service.get('Lines', {}).get('Line', [])

		if isinstance(_line_list, list):
			for _line in _line_list:
				_line_names.append(_line.get('LineName', ''))

		else:
			_line_names.append(_line_list.get('LineName', ''))

		_standard_service = _service.get('StandardService', {})
		_origin = _standard_service.get('Origin', '')
		_dest = _standard_service.get('Destination', '')

		_slug = create_slug(_line_names, _origin, _dest)

		if 'StopPoint' in _stop_points:
			_stops = _stop_points.get('StopPoint', [])

			_stops = as_list(_stops)

			for _stop in _stops:
				_is_naptan = False
				_naptan_code, _indicator, _compass, _timing_status = '', '', '', ''

				_name = _stop.get('Descriptor', {}).get('CommonName', '')
				_locality_ref = _stop.get('Place', {}).get('NptgLocalityRef', '')

				# if _locality_ref in _locality_list:
				# 	# _locality_name = _locality_list.get(_locality_ref, {}).get('Descriptor', {}).get('LocalityName', '')
				# 	_locality_name = _locality_list[_locality_ref].get('name', {})

				_atco_code = _stop.get('AtcoCode', '')

				# if _atco_code in _naptan_list:
				# 	_is_naptan = True

				# 	_response = retry_request(f'https://xavier114fch.github.io/naptan/data/naptan/stopPoints/{_atco_code}.json')
				# 	_naptan = _response.json()

				# 	_naptan_code = _naptan.get(_atco_code, {}).get('naptanCode', '')
				# 	_indicator = _naptan.get(_atco_code, {}).get('indicator', '')
				# 	_compass = _naptan.get(_atco_code, {}).get('properties', {},).get('bearing', '')
				# 	_timing_status = _naptan.get(_atco_code, {}).get('properties', {},).get('timingStatus', '')
				# 	_coords = _naptan.get(_atco_code, {}).get('coordinates', [])

				if _atco_code not in _all_stops:
					_all_stops.setdefault(_atco_code, {
						# 'naptanCode': _naptan_code,
						'name': _name,
						'localityRef': _locality_ref,
						# 'localityName': _locality_name,
						# 'indicator': _indicator,
						# 'compass': _compass,
						# 'timingStatus': _timing_status,
						'slugs': [_slug],
						# 'coordinates': _coords
						# 'isNaptan': _is_naptan
					})

				else:
					_slugs = _all_stops.get(_atco_code, {}).get('slugs', [])
					if _slug not in _slugs:
						_slugs.append(_slug)

		elif 'AnnotatedStopPointRef' in _stop_points:
			_stops = _stop_points.get('AnnotatedStopPointRef', [])

			_stops = as_list(_stops)

			for _stop in _stops:
				_is_naptan = False
				_naptan_code, _indicator, _compass, _timing_status = '', '', '', ''

				_name = _stop.get('CommonName', '')
				_locality_name = _stop.get('LocalityName', '')
				_atco_code = _stop.get('StopPointRef', '')

				# if _atco_code in _naptan_list:
				# 	_is_naptan = True

				# 	_response = retry_request(f'https://xavier114fch.github.io/naptan/data/naptan//stopPoints/{_atco_code}.json')
				# 	_naptan = _response.json()

				# 	_naptan_code = _naptan.get(_atco_code, {}).get('naptanCode', '')
				# 	_indicator = _naptan.get(_atco_code, {}).get('indicator', '')
				# 	_compass = _naptan.get(_atco_code, {}).get('properties', {},).get('bearing', '')
				# 	_timing_status = _naptan.get(_atco_code, {}).get('properties', {},).get('timingStatus', '')
				#	_coords = _naptan.get(_atco_code, {}).get('coordinates', '')

				if _atco_code not in _all_stops:
					_all_stops.setdefault(_atco_code, {
						# 'naptanCode': _naptan_code,
						'name': _name,
						'localityRef': _locality_ref,
						# 'localityName': _locality_name,
						# 'indicator': _indicator,
						# 'compass': _compass,
						# 'timingStatus': _timing_status,
						'slugs': [_slug],
						# 'coordinates': _coords
						# 'isNaptan': _is_naptan
					})

				else:
					_slugs = _all_stops.get(_atco_code, {}).get('slugs', [])
					if _slug not in _slugs:
						_slugs.append(_slug)
		
		else:
			logger.warning(f'{_directory}/{_file} does not have any stop points.')

def split_all_stops(_data_dir: str, _all_stops: dict) -> None:
	logger.info(f'Created {len(_all_stops)} stops.')
	logger.info('Splitting StopPoints ...')

	os.makedirs(f'{_data_dir}/stopPoints', exist_ok=True)
	for _k, _v in _all_stops.items():
		_d = {}
		_d[_k] = _v

		with open(os.path.join(f'{_data_dir}/stopPoints', f'{_k}.json'), 'w') as f:
			f.write(json.dumps(_d, ensure_ascii = False, separators=(',', ':'), sort_keys=True))
	
	logger.info('=====')

def main():
	fetch_tnds_data(_data_dir)
	convert_tnds(_data_dir)
	split_all_stops(_data_dir, _all_stops)

if __name__ == "__main__":
	main()
