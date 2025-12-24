import os, zipfile, json, xmltodict, re, requests
import xml.etree.ElementTree as ET
from ftplib import FTP, error_temp, error_perm, all_errors
from datetime import datetime, timedelta
from tenacity import retry, wait_fixed, stop_after_attempt, retry_if_exception_type

_data_dir = 'data/tnds'
_nptg_dir = 'data/nptg'
_naptan_dir = 'data/naptan'

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

def isFTPAlive(ftp: FTP) -> bool:
	try:
		if ftp.sock is None:
			print(f'FTP connection is closed')
			return False

		ftp.voidcmd('NOOP')
		return True

	except all_errors as e:
		print(f'FTP connection is dropped: {e}')
		return False

@retry(
	wait = wait_fixed(5),
	stop = stop_after_attempt(10),
	retry = retry_if_exception_type((ConnectionResetError, error_temp))
)
def fetchTndsData(_data_dir):
	# FTP server details
	_ftp_host = 'ftp.tnds.basemap.co.uk'
	_ftp_username = os.environ.get('TNDS_FTP_USER')
	_ftp_password = os.environ.get('TNDS_FTP_PWD')

	# Test env variables
	if not _ftp_username or not _ftp_password:
		raise RuntimeError('Missing FTP credentials from env variables.')

	# Connect to the FTP server
	_ftp = FTP(_ftp_host, timeout=30)
	_ftp.login(_ftp_username, _ftp_password)
	_ftp.set_pasv(True) # Enable passive mode

	# Navigate to the desired directory
	_ftp.cwd('/TNDSV2.5')

	# List the files in the directory
	_file_list = _ftp.nlst()

	# Fetch each file
	for _file_name in _file_list:
		if not isFTPAlive(_ftp):
			# Connect to the FTP server
			_ftp = FTP(_ftp_host, timeout=60)
			_ftp.login(_ftp_username, _ftp_password)
			_ftp.set_pasv(True) # Enable passive mode

		_response = _ftp.sendcmd(f'MDTM {_file_name}')
		_remote_timestamp = datetime.strptime(_response[4:], '%Y%m%d%H%M%S')

		_local_file_path = f'{_data_dir}/{_file_name}'
		_local_timestamp = datetime.fromtimestamp(os.path.getmtime(_local_file_path)) if os.path.exists(_local_file_path) else None

		if _local_timestamp is None or _remote_timestamp > _local_timestamp:
			print(f'Getting {_file_name} from TNDS FTP ...')
			os.makedirs(_data_dir, exist_ok=True)
			with open(_local_file_path, 'wb') as _local_file:
				if not isFTPAlive(_ftp):
					# Connect to the FTP server
					_ftp = FTP(_ftp_host, timeout=60)
					_ftp.login(_ftp_username, _ftp_password)
					_ftp.set_pasv(True) # Enable passive mode

				_ftp.retrbinary(f'RETR {_file_name}', _local_file.write)
				# _ftp.quit()

			if _file_name.endswith('.zip'):
				print(f'Unzipping {_file_name} ...')
				_zip_file_path = os.path.join(_data_dir, _file_name)
				extractZip(_zip_file_path, _data_dir)
				# convertTnds(_data_dir, os.path.splitext(os.path.basename(_file_name))[0])

		else:
			print(f'{_file_name} is up to date.')

	# Disconnect from the FTP server
	if isFTPAlive(_ftp):
		_ftp.quit()
	print('=====')


# Function to extract a ZIP file into a directory with the same name
def extractZip(zip_file_path, extract_directory):
	with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
		zip_name = os.path.splitext(os.path.basename(zip_file_path))[0]
		extract_path = os.path.join(extract_directory, zip_name)
		zip_ref.extractall(extract_path)

def convertTnds(_data_dir):
	_directories = sorted([_item for _item in os.listdir(_data_dir) if os.path.isdir(os.path.join(_data_dir, _item)) and _item != 'stopPoints'])

	for _directory in _directories:
		print(f'Converting TNDS XML files in {_directory} ...')

		# NCSD XMLs are in one level deeper
		_dir = f'{_data_dir}/{_directory}/{_directory}_TXC' if _directory == 'NCSD' else f'{_data_dir}/{_directory}'

		_total_count = 0

		for _file in sorted(os.listdir(_dir)):
			if _file.endswith('.xml'):
				# print(f'Converting TNDS XML file {_dir}/{_file} ...')
				_total_count = _total_count + 1

				with open(os.path.join(_dir, _file), 'r') as f:
					_content = f.read()
					_data = json.dumps(xmltodict.parse(_content), ensure_ascii=False, separators=(',', ':'))
					_data = _data.replace('@', '')

					with open(os.path.join(_dir, f'_{os.path.splitext(_file)[0]}.json'), 'w') as f:
						f.write(_data)
					
		print(f'Processed {_total_count} files.')

	print('=====')

def collectPreviousSlugs(_data_dir) -> dict:
	_all_slugs = {}

	_directories = sorted([_item for _item in os.listdir(_data_dir) if os.path.isdir(os.path.join(_data_dir, _item)) and _item != 'stopPoints'])

	for _directory in _directories:
		print(f'Getting previous slugs in {_directory} ...')

		# NCSD XMLs are in one level deeper
		_dir = f'{_data_dir}/{_directory}/{_directory}_TXC' if _directory == 'NCSD' else f'{_data_dir}/{_directory}'

		for _file in sorted(os.listdir(_dir)):
			if not _file.startswith('_') and _file.endswith('.json'):
				with open(os.path.join(_dir, _file), 'r') as f:
					_data = json.load(f)

					for _slug, _values in _data.items():
						_all_slugs.setdefault(_slug, [])

						for _v in _values:
							_all_slugs[_slug].append({
								'filename': _v.get('filename')[1:],
								'mode': _v.get('mode'),
								'region': _v.get('region'),
								'name': _v.get('name'),
								'description': _v.get('description'),
								'operators': _v.get('operators'),
								'lastModified': _v.get('lastModified'),
								'publicUse': _v.get('publicUse'),
								'startDate': _v.get('startDate'),
								'endDate': _v.get('endDate'),
							})

	_len = len(_all_slugs)
	print(f'Collected {_len} previous slugs.')
	print('=====')
	return _all_slugs

def outputTnds(_data_dir):
	# _all_slugs = {}
	_directories = sorted([_item for _item in os.listdir(_data_dir) if os.path.isdir(os.path.join(_data_dir, _item))])

	for _directory in _directories:
		print(f'Creating route files in {_directory} ...')

		# NCSD XMLs are in one level deeper
		_dir = f'{_data_dir}/{_directory}/{_directory}_TXC' if _directory == 'NCSD' else f'{_data_dir}/{_directory}'

		for _file in sorted(os.listdir(_dir)):
			if _file.startswith('_') and _file.endswith('.json'):
				with open(os.path.join(_dir, _file), 'r') as f:
					# print(f'{_directory}/{_file}')

					_data = json.load(f)

					_data = _data.get('TransXChange', {})
					_last_modified = _data.get('ModificationDateTime', '')
					_services = _data.get('Services', {}).get('Service', {})

					if not _services:
						print(f'{_directory}/{_file} has no services.')

					# elif isinstance(_services, list):
					# 	print(f'{_directory}/{_file} has mulitple services.')

					if not isinstance(_services, list):
						_services = [_services]

					_single_service = {}

					for _service in _services:
						_start_date = _service.get('OperatingPeriod', {}).get('StartDate', '')
						_end_date = _service.get('OperatingPeriod', {}).get('EndDate', '')

						if not compareDates(_start_date, _end_date):
							continue

						_line_names, _line_ids, _vias, _noc, _routes = [], [], [], [], []
						_journey_patterns, _serviced_organisations, _vehicles = {}, {}, {}

						_line_list = _service.get('Lines', {}).get('Line', [])

						if not _line_list:
							print(f'{_directory}/{_file} has no lines.')

						# elif isinstance(_line_list, list):
						# 	print(f'{_directory}/{_file} has mulitple lines.')

						if not isinstance(_line_list, list):
							_line_list = [_line_list]

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

						if isinstance(_vias_list, list):
							_vias = _vias_list

						else:
							_vias = [_vias_list]

						_journey_pattern_list = _standard_service.get('JourneyPattern', [])

						if not isinstance(_journey_pattern_list, list):
							_journey_pattern_list = [_journey_pattern_list]

						for _jp in _journey_pattern_list:
							_journey_pattern = {}

							_jp_key = _jp.get('RouteRef', None)

							if not _jp_key:
								_jp_key = _jp.get('id', None)

							if _jp_key:
								_journey_patterns.setdefault(_jp_key, [])

							else:
								print(f'{_directory}/{_file}: Empty journey pattern key.')

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

							if not isinstance(_journey_pattern_section_list, list):
								_journey_pattern_section_list = [_journey_pattern_section_list]

							_journey_pattern_section_ref_list = _jp.get('JourneyPatternSectionRefs', [])
							if not isinstance(_journey_pattern_section_ref_list, list):
								_journey_pattern_section_ref_list = [_journey_pattern_section_ref_list]

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

										if not isinstance(_journey_pattern_timing_link_list, list):
											_journey_pattern_timing_link_list = [_journey_pattern_timing_link_list]

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
												_activities.append(_jptl.get('To', {}).get('Activity', 'pickUpAndSetDown'))

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
							_journey_pattern.setdefault('sequenceNumber', _sequences)
							_journey_pattern.setdefault('dynamicDestinationDisplay', _display)

							_vehicle_journey_list = _data.get('VehicleJourneys', {}).get('VehicleJourney', [])

							if not isinstance(_vehicle_journey_list, list):
								_vehicle_journey_list = [_vehicle_journey_list]

							_departures = []
							for _vj in _vehicle_journey_list:
								_journey_pattern.setdefault('lineId', _vj.get('LineRef', ''))
								_notes = _vj.get('Note', [])

								if not isinstance(_notes, list):
									_notes = [_notes]

								for _note in _notes:
									_journey_pattern.setdefault('note', []).append(_note.get('NoteText', ''))

								_operating_profile = {}

								_days_of_week = _vj.get('OperatingProfile', {}).get('RegularDayType', {}).get('DaysOfWeek', {}) or _service.get('OperatingProfile', {}).get('RegularDayType', {}).get('DaysOfWeek', {})

								if _days_of_week:
									_operating_profile.setdefault('regular', list(_days_of_week.keys()))

								_days_of_operation_list = (_vj.get('OperatingProfile', {}).get('SpecialDaysOperation', {}) or {}).get('DaysOfOperation', {}).get('DateRange', []) or (_service.get('OperatingProfile', {}).get('SpecialDaysOperation', {}) or {}).get('DaysOfOperation', {}).get('DateRange', [])

								if _days_of_operation_list:
									_special = _operating_profile.setdefault('special', {})
									_running = _special.setdefault('running', [])

									if not isinstance(_days_of_operation_list, list):
										_days_of_operation_list = [_days_of_operation_list]

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

									if not isinstance(_days_of_non_operation_list, list):
										_days_of_non_operation_list = [_days_of_non_operation_list]

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

									if not isinstance(_other_public_holiday_list, list):
										_other_public_holiday_list = [_other_public_holiday_list]

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
									
									if not isinstance(_other_public_holiday_list, list):
										_other_public_holiday_list = [_other_public_holiday_list]

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

									if not isinstance(_days_of_operation, list):
										_days_of_operation = [_days_of_operation]

									_running.extend(_days_of_operation)

								_days_of_non_operation = _vj.get('OperatingProfile', {}).get('ServicedOrganisationDayType', {}).get('DaysOfNonOperation', []) or _service.get('OperatingProfile', {}).get('ServicedOrganisationDayType', {}).get('DaysOfNonOperation', [])

								if _days_of_non_operation:
									_serviced_orgs = _operating_profile.setdefault('servicedOrganisations', {})
									_not_running = _serviced_orgs.setdefault('notRunning', [])

									if not isinstance(_days_of_non_operation, list):
										_days_of_non_operation = [_days_of_non_operation]

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
												if compareDicts(_departure.get('profiles'), _operating_profile) and _departure_time not in _departure.setdefault('departures', []):
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
										if compareDicts(_departure.get('profiles'), _operating_profile) and _departure_time not in _departure.setdefault('departures', []):
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
									if not isinstance(_vjtl_list, list):
										_vjtl_list = [_vjtl_list]

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

						if not isinstance(_operators_list, list):
							_operators_list = [_operators_list]

						for _operator in _operators_list:
							_noc.append(_operator.get('NationalOperatorCode', _operator.get('OperatorCode', '')))

						_route_list = _data.get('Routes', {}).get('Route', [])
						_journey_pattern_list = _service.get('StandardService', {}).get('JourneyPattern', [])

						if _route_list:
							if not isinstance(_route_list, list):
								_route_list = [_route_list]

							for _route in _route_list:
								_route_id = _route.get('id', '')
								_route_section_ref = _route.get('RouteSectionRef', [])
								_route_link_ids, _stop_points, _distance, _tracks, _direction = [], [], [], [], []

								if not isinstance(_route_section_ref, list):
									_route_section_ref = [_route_section_ref]

								_route_sections = _data.get('RouteSections', {}).get('RouteSection', [])

								if not isinstance (_route_sections, list):
									_route_sections = [_route_sections]

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

							if not isinstance(_holiday_list, list):
								_holiday_list = [_holiday_list]

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

							if not isinstance(_working_day_list, list):
								_working_day_list = [_working_day_list]

							for _working_day in _working_day_list:
								_working_days.append({
									'startDate': _working_day.get('StartDate', ''),
									'endDate': _working_day.get('EndDate', ''),
									'description': _working_day.get('Description', '')
								})

							if _working_days:
								_org_code.setdefault('workingDays', _working_days)

						_line_name_list = '+'.join(_line_names)
						_safe_chars = re.compile(r'[^a-zA-Z0-9\-\+\.\|]')
						_slug = (f'{_line_name_list}-{_origin.replace(' / ', ' ').replace(' ', '-')}-{_dest.replace(' / ', ' ').replace(' ', '-')}').lower()
						_slug = _safe_chars.sub('', _slug)
						# print(f'{_directory}/{_file}: [{_mode}][{_noc}] {_slug}')

						_single_service.setdefault(_slug, [])
						_single_service[_slug].append({
							'filename': _file,
							'mode': _mode,
							'region': _directory,
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


					if _single_service is not {}:
						with open(os.path.join(_dir, f'{_file[1:]}'), 'w') as f:
							f.write(json.dumps(_single_service, ensure_ascii = False, separators=(',', ':')))

	# with open(os.path.join(_data_dir, 'all_slugs.json'), 'w') as f:
	# 	f.write(json.dumps(_all_slugs, ensure_ascii = False, separators=(',', ':')))
	# 	_len = len(_all_slugs)
	# 	print(f'Created {_len} slugs.')
	print('=====')

def mergeSlugs(_data_dir, _previous_slugs):
	_current_slugs = {}
	_merged_slugs = {}

	with open(os.path.join(f'{_data_dir}','all_slugs.json'), 'r') as f:
		_current_slugs = json.load(f)

	for _k, _v in _current_slugs.items():
		_merged_slugs[_k] = _v

	for _k, _v in _previous_slugs.items():
		if _k not in _merged_slugs:
			_new_v = []

			for _item in _v:
				_start_date = _item.get('startDate', None)
				_end_date = _item.get('endDate', None)

				if compareDates(_start_date, _end_date):
					_new_v.append(_item)

			if _new_v:
				_merged_slugs[_k] = _new_v

	_merged_slugs = dict(sorted(_merged_slugs.items()))

	with open(os.path.join(_data_dir, 'all_slugs.json'), 'w') as f:
		f.write(json.dumps(_merged_slugs, ensure_ascii = False, separators=(',', ':')))
		_len = len(_merged_slugs)
		print(f'Merged {_len} slugs.')

def getStopPointsFromTnds(_data_dir):
	_all_stops = {}

	_directories = sorted([_item for _item in os.listdir(_data_dir) if os.path.isdir(os.path.join(_data_dir, _item)) and _item != 'stopPoints'])

	for _directory in _directories:
		print(f'Getting stop points in {_directory} ...')

		# NCSD XMLs are in one level deeper
		_dir = f'{_data_dir}/{_directory}/{_directory}_TXC' if _directory == 'NCSD' else f'{_data_dir}/{_directory}'

		for _file in sorted(os.listdir(_dir)):
			if _file.startswith('_') and _file.endswith('.json'):
				with open(os.path.join(_dir, _file), 'r') as f:
					# print(f'{_directory}/{_file}')

					_data = json.load(f)

					_stop_points = _data.get('TransXChange', {}).get('StopPoints', {})
					_atco_code, _name, _locality_ref, _locality_name, _origin, _dest, _slug = '', '', '', '', '', '', ''
					_line_names = []

					_services = _data.get('TransXChange', {}).get('Services', {}).get('Service', {})

					if not isinstance(_services, list):
						_services = [_services]

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

						_line_name_list = '+'.join(_line_names)
						_safe_chars = re.compile(r'[^a-zA-Z0-9\-\+]')
						_slug = (f'{_line_name_list}-{_origin.replace(' / ', ' ').replace(' ', '-')}-{_dest.replace(' / ', ' ').replace(' ', '-')}').lower()
						_slug = _safe_chars.sub('', _slug)

						if 'StopPoint' in _stop_points:
							_stops = _stop_points.get('StopPoint', [])

							if not isinstance(_stops, list):
								_stops = [_stops]

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

								# 	_response = retryRequest(f'https://xavier114fch.github.io/naptan/data/naptan/stopPoints/{_atco_code}.json')
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

							if not isinstance(_stops, list):
								_stops = [_stops]

							for _stop in _stops:
								_is_naptan = False
								_naptan_code, _indicator, _compass, _timing_status = '', '', '', ''

								_name = _stop.get('CommonName', '')
								_locality_name = _stop.get('LocalityName', '')
								_atco_code = _stop.get('StopPointRef', '')

								# if _atco_code in _naptan_list:
								# 	_is_naptan = True

								# 	_response = retryRequest(f'https://xavier114fch.github.io/naptan/data/naptan//stopPoints/{_atco_code}.json')
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
							print(f'{_data_dir}/{_directory}/{_file} does not have any stop points.') 

		# with open(os.path.join(_data_dir, 'all_stop_points.json'), 'w') as f:
		# 	f.write(json.dumps(_all_stops, ensure_ascii = False, separators=(',', ':')))
		# 	_len = len(_all_stops)
		# 	print(f'Created {_len} stops.')

	# with open(os.path.join(f'{_data_dir}', f'all_stop_points.json'), 'w') as f:
	# 	f.write(json.dumps(list(_all_stops.keys()), ensure_ascii = False, separators=(',', ':')))
	_len = len(_all_stops)
	print(f'Created {_len} stops.')
	print('=====')

	print('Splitting StopPoints ...')
	os.makedirs(f'{_data_dir}/stopPoints', exist_ok=True)
	for _k, _v in _all_stops.items():
		_d = {}
		_d[_k] = _v

		with open(os.path.join(f'{_data_dir}/stopPoints', f'{_k}.json'), 'w') as f:
			f.write(json.dumps(_d, ensure_ascii = False, separators=(',', ':')))
	
	print('=====')

def mergeStopPoints(_data_dir, _previous_slugs):
	_all_stops_from_previous = {}

	for _slug, _services in _previous_slugs.items():
		for _service in _services:
			_routes = _service.get('routes', [])

			for _route in _routes:
				_stop_points = _route.get('stopPoints', [])
				
				for _stop in _stop_points:
					if _stop not in _all_stops_from_previous:
						_all_stops_from_previous.setdefault(_stop, {
							'name': '',
							'localityRef': '',
							'slugs': [_slug]
						})

					else:
						_slugs = _all_stops_from_previous.get(_stop, {}).get('slugs', [])
						if _slug not in _slugs:
							_slugs.append(_slug)

	_previous_stops = list(_all_stops_from_previous.keys())
	_len = len(_previous_stops)
	print(f'Collected {_len} previous stops.')

	_current_stops = []
	with open(os.path.join(f'{_data_dir}','all_stop_points.json'), 'r') as f:
		_current_stops = json.load(f)

	_len = len(_current_stops)
	print(f'Collected {_len} current stops.')

	_common_stops = list(set(_previous_stops) & set(_current_stops))
	_len = len(_current_stops)
	print(f'There are {_len} common stops.')

	_stops_only_in_previous = {_k: _v for _k, _v in _all_stops_from_previous.items() if _k not in _common_stops}
	_len = len(list(_stops_only_in_previous.keys()))
	print(f'There are {_len} stops exist only in previous.')

	_merged_stops = list(set(_previous_stops) | set(_current_stops))
	_len = len(_merged_stops)
	print(f'There are {_len} merged stops.')

	for _stop in _common_stops:
		_d = {}

		with open(os.path.join(f'{_data_dir}/stopPoints', f'{_stop}.json'), 'r') as f:
			_d = json.load(f)

		_p_slugs = _all_stops_from_previous.get(_stop, {}).get('slugs', [])
		_c_slugs = _d.get(_stop, {}).get('slugs', [])
		_m_slugs = list(set(_p_slugs) | set(_c_slugs))

		_d[_stop]['slugs'] = _m_slugs

		with open(os.path.join(f'{_data_dir}/stopPoints', f'{_stop}.json'), 'w') as f:
			f.write(json.dumps(_d, ensure_ascii = False, separators=(',', ':')))

	for _k, _v in _stops_only_in_previous.items():
		_d = {}
		_d[_k] = _v

		with open(os.path.join(f'{_data_dir}/stopPoints', f'{_k}.json'), 'w') as f:
			f.write(json.dumps(_d, ensure_ascii = False, separators=(',', ':')))

	with open(os.path.join(f'{_data_dir}', f'all_stop_points.json'), 'w') as f:
		f.write(json.dumps(_merged_stops, ensure_ascii = False, separators=(',', ':')))

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
			# with open(os.path.join(f'{_naptan_dir}','naptan_stop_points_all.json'), 'r') as f:
			# 	_naptan_list = json.load(f)

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
		# _stops_in_naptan = {_k:_v for _k, _v in _naptan_list.items() if _k not in _common_naptan}

		with open(os.path.join(_data_dir, 'stops_tnds_only.json'), 'w') as f:
			f.write(json.dumps(_stops_in_tnds, ensure_ascii = False, separators=(',', ':')))
			print(f'There are {len(_stops_in_tnds)} stop points only appear in TNDS')
			print('=====')

		# print('Stops only in TNDS:')
		# print(list(_stops_in_tnds.keys()))
		# print('===')
		# print('Stops only in Naptan:')
		# print(list(_stops_in_naptan.keys()))

def generateTimetables(_data_dir):
	_directories = sorted([_item for _item in os.listdir(_data_dir) if os.path.isdir(os.path.join(_data_dir, _item))])
	_runtime_pattern = re.compile(r'-?PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')
	_waittime_pattern = re.compile(r'-?PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')

	for _directory in _directories:
		print(f'Generating timetables in {_directory} ...')

		# NCSD XMLs are in one level deeper
		_dir = f'{_data_dir}/{_directory}/{_directory}_TXC' if _directory == 'NCSD' else f'{_data_dir}/{_directory}'

		for _file in sorted(os.listdir(_dir)):
			if not _file.startswith('_') and _file.endswith('.json'):
				with open(os.path.join(_dir, _file), 'r') as f:
					# print(f'{_directory}/{_file}')

					_data = json.load(f)

					for _slug, _slug_items in _data.items():
						for _v in _slug_items:
							_output = {}

							_routes = _v.get('routes', [])
							_route_ids = [_route.get('routeId') for _route in _routes]
							_timetables = _v.get('timetables', {})

							for _timetable_route_id, _timetable in _timetables.items():
								if _timetable_route_id in _route_ids:
									_stop_points = [_stop for _route in _routes if _timetable_route_id == _route.get('routeId') for _stop in _route.get('stopPoints')]
									_route_id = _output.setdefault(_timetable_route_id, {})

									for _t in _timetable:
										_runtimes = _t.get('runtimes', [])
										_waittimes = _t.get('waitTimes', [])
										_schedules = _t.get('schedules', [])

										if len(_stop_points) > 0 and len(_waittimes) > 0 and len(_stop_points) != len(_waittimes):
											print(f'{_directory}/{_file} - {_timetable_route_id} has different stop points and wait times lengths: {len(_stop_points)} vs {len(_waittimes)}.')
											continue

										for _schedule in _schedules:
											_profiles = _schedule.get('profiles', {})
											_departures = _schedule.get('departures', [])
											_dayshifts = _schedule.get('dayShift', [])

											_regulars = _profiles.get('regular', [])
											_holidays_running = _profiles.get('holidays', {}).get('running', [])
											_holidays_not_running = _profiles.get('holidays', {}).get('notRunning', [])
											_special_running = _profiles.get('special', {}).get('running', {})
											_special_not_running = _profiles.get('special', {}).get('notRunning', {})

											for _i, _departure in enumerate(_departures):
												_departure_list = []
												_dayshift = '*' if _dayshifts[_i] == 1 else ''
												_previous_day = True if _dayshifts[_i] == 1 else False

												for _j , _wait_time in enumerate(_waittimes):
													_original = datetime.strptime(_departure, '%H:%M:%S')

													_wait_match = _waittime_pattern.match(_wait_time if _wait_time != '' else 'PT0S')

													if not _wait_match:
														raise ValueError(f'Invalid wait time {_directory}/{_file}.')

													_wait_hours = int(_wait_match.group(1) or 0)
													_wait_minutes = int(_wait_match.group(2) or 0)
													_wait_seconds = int(_wait_match.group(3) or 0)
													_wait_delta = timedelta(hours=_wait_hours, minutes=_wait_minutes, seconds=_wait_seconds)

													_with_wait = _original + _wait_delta
													_total_seconds = (_with_wait - _original.replace(hour=0, minute=0, second=0)).total_seconds()
													_total_hours = int(_total_seconds // 3600)
													_dayshift_with_wait = '*' if _total_hours > 23 or _previous_day else ''
													_previous_day = True if _total_hours > 23 or _previous_day else ''

													_run_time = _runtimes[_j - 1] if _j > 0 else 'PT0S'
													_run_match = _runtime_pattern.match(_run_time)

													if not _run_match:
														raise ValueError(f'Invalid runtime {_directory}/{_file}: {_run_time}')

													_run_hours = int(_run_match.group(1) or 0)
													_run_minutes = int(_run_match.group(2) or 0)
													_run_seconds = int(_run_match.group(3) or 0)
													_run_delta = timedelta(hours=_run_hours, minutes=_run_minutes, seconds=_run_seconds)

													_with_run = _with_wait + _run_delta
													_total_seconds = (_with_run - _original.replace(hour=0, minute=0, second=0)).total_seconds()
													_total_hours = int(_total_seconds // 3600)
													_dayshift_with_run = '*' if _total_hours > 23 or _previous_day else ''
													_previous_day = True if _total_hours > 23 or _previous_day else ''

													_time_o = _original.time().strftime('%H:%M:%S') + _dayshift
													_time_w = _with_wait.time().strftime('%H:%M:%S') + _dayshift_with_wait
													_time_r = _with_run.time().strftime('%H:%M:%S') + _dayshift_with_run

													_departure = _with_run.time().strftime('%H:%M:%S')

													# print(f'{_departure}, {_dayshift}, {_wait_time}, {_with_wait}, {_dayshift_with_wait}, {_run_time}, {_with_run}. {_dayshift_with_run}')

													if _j == 0:
														_departure_list.append({
															'stop': _stop_points[_j],
															'time': _time_o if _wait_time == '' else f'{_time_o}|{_time_w}'
														})

													if _j > 0:
														_departure_list.append({
															'stop': _stop_points[_j],
															'time': _time_r if _wait_time == '' else f'{_time_w}|{_time_r}'
														})
														
											for _regular in _regulars:
												if _regular in ['Monday', 'MondayToFriday', 'MondayToSaturday', 'MondayToSunday', 'NotSaturday']:
													_monday = _route_id.setdefault('Monday', [])
													_monday.append(_departure_list)

												if _regular in ['Tuesday', 'MondayToFriday', 'MondayToSaturday', 'MondayToSunday', 'NotSaturday']:
													_tuesday = _route_id.setdefault('Tuesday', [])
													_tuesday.append(_departure_list)

												if _regular in ['Wednesday', 'MondayToFriday', 'MondayToSaturday', 'MondayToSunday', 'NotSaturday']:
													_wednesday = _route_id.setdefault('Wednesday', [])
													_wednesday.append(_departure_list)

												if _regular in ['Thursday', 'MondayToFriday', 'MondayToSaturday', 'MondayToSunday', 'NotSaturday']:
													_thursday = _route_id.setdefault('Thursday', [])
													_thursday.append(_departure_list)

												if _regular in ['Friday', 'MondayToFriday', 'MondayToSaturday', 'MondayToSunday', 'NotSaturday']:
													_friday = _route_id.setdefault('Friday', [])
													_friday.append(_departure_list)

												if _regular in ['Saturday', 'MondayToSaturday', 'MondayToSunday', 'Weekend']:
													_saturday = _route_id.setdefault('Saturday', [])
													_saturday.append(_departure_list)

												if _regular in ['Sunday', 'MondayToSunday', 'NotSaturday', 'Weekend']:
													_sunday = _route_id.setdefault('Sunday', [])
													_sunday.append(_departure_list)

						# print(_output)

def compareDicts(_x, _y) -> bool:
	if sorted(_x.keys()) != sorted(_y.keys()):
		return False

	for _key in _x:
		if _x[_key] != _y[_key]:
			return False

	return True

def compareDates(_start, _end) -> bool:
	_today = datetime.today().date()
	_start = datetime.fromisoformat(_start).date() if _start and _start != '' else None
	_end = datetime.fromisoformat(_end).date() if _end and _end != '' else None

	return (_start and _today < _start) or (_start and _end and _start <= _today <= _end) or (_start and not _end and _today >= _start)

def main():
	# _previous_slugs = collectPreviousSlugs('gh-pages-data/data/tnds')

	fetchTndsData(_data_dir)
	convertTnds(_data_dir)
	outputTnds(_data_dir)
	# mergeSlugs(_data_dir, _previous_slugs)
	getStopPointsFromTnds(_data_dir)
	# mergeStopPoints(_data_dir, _previous_slugs)
	# compareStopPoints(_data_dir)
	# generateTimetables(_data_dir)

if __name__ == "__main__":
	main()