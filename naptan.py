import time, os, requests, json, xmltodict, re
from pyproj import Transformer

data_dir = 'data/naptan'
nptg_dir = 'data/nptg'

_transformer = Transformer.from_crs(27700, 4326, always_xy=True)

_stops_all, _stop_areas_all = {}, {}

_geodata_stops_all = {
	'type': 'FeatureCollection',
	'features': []
}

_geodata_areas_all = {
	'type': 'FeatureCollection',
	'features': []
}

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

def getAtcoList():
	try:
		_data = retryRequest('https://github.com/xavier114fch/uk-bus-open-data/raw/gh-pages/data/nptg/nptg_atcoareas.json')
		# with open(os.path.join(nptg_dir, 'nptg_atcoareas.json'), 'r') as f:
		# 	_data = json.load(f)

	except Exception as err:
		print('Cannot fetch NPTG data to obtain ATCO list. Retrying ...')
		time.sleep(10)
		getAtcoList()

	else:
		_data = _data.json()
		return sorted(_data.keys())

def getNaptan(atco):
	global _geodata_stops_all
	global _geodata_areas_all

	def fetchNaptanData(atco):
		try:
			_data = retryRequest(f'https://naptan.api.dft.gov.uk/v1/access-nodes?atcoAreaCodes={atco}&dataFormat=xml')

		except Exception as err:
			print(f'Cannot fetch Naptan data of ATCO {atco}. {err}')
			# time.sleep(10)
			# fetchNaptanData(atco)
			return None

		else:
			os.makedirs(data_dir, exist_ok=True)
			# with open(os.path.join(data_dir, f'naptan_{atco}.xml'), 'wb') as f:
			# 	f.write(_data.content)

			return _data.content

	def flatten(json_obj, prefix=''):
		_flattened_dict = {}

		for _key, _value in json_obj.items():
			_new_key = f'{prefix}.{_key}' if prefix else _key

			if isinstance(_value, dict):
				_flattened_dict.update(flatten(_value, _new_key))

			else:
				_flattened_dict[_new_key] = _value

		return _flattened_dict

	def toCamelCase(str):
		return str[0].lower() + str[1:]

	def convertKeytoCamelCase(data):
		if isinstance(data, dict):
			_new_data = {}

			for _key, _value in data.items():
				_new_key = toCamelCase(_key)
				_new_data[_new_key] = convertKeytoCamelCase(_value)
			return _new_data

		if isinstance(data, list):
			return [convertKeytoCamelCase(_item) for _item in data]

		return data

	def openNptgLocalities():
		global _locality_list
		try:
			_response = retryRequest('https://raw.githubusercontent.com/xavier114fch/uk-bus-open-data/refs/heads/gh-pages/data/nptg/nptg_localities.json')
			_locality_list = _response.json()
			# with open(os.path.join(f'{nptg_dir}','nptg_localities.json'), 'r') as f:
			# 	_locality_list = json.load(f)

		except BaseException:
			print('Cannot open NPTG locality list.')

		else:
			return True

	try:
		openNptgLocalities()

	except BaseException:
		pass

	else:
		print(f'Getting NaPTAN XML of ATCO {atco} from API ...')
		_data = fetchNaptanData(atco)

		if _data is not None:
			print('Converting to JSON ...')
			_data = json.dumps(xmltodict.parse(_data), ensure_ascii = False, separators=(',', ':'))
			_pattern = r'{(?:\'|")@xml:lang(?:\'|"):(?:\'|")en(?:\'|"),(?:\'|")#text(?:\'|"):(?:\'|")(.*?)(?:\'|")}'
			_data = re.sub(_pattern, r'"\1"', _data)
			_data = _data.replace('@', '')

			# with open(os.path.join(data_dir, f'naptan_{atco}.json'), 'w') as f:
			# 	f.write(_data)

			print('Creating GeoJSON for StopPoints ...')
			_data = json.loads(_data)

			_new_data = {}

			_geodata = {
				'type': 'FeatureCollection',
				'features': []
			}

			def appendStopPoint(point):
				global _stops_all
				global _geodata_stops_all

				_location = point.get('Place', {}).get('Location', {})
				_location = _location.get('Translation', _location)

				if _location.get('Longitude') not in [None, '0.000000000'] and _location.get('Latitude') not in [None, '0.000000000']:
					_lon, _lat = _location.get('Longitude'), _location.get('Latitude')

				elif 'Easting' in _location and 'Northing' in _location:
					_lon, _lat = _transformer.transform(_location.get('Easting'),_location.get('Northing'))

				else:
					return

				_atco_code = point.get('AtcoCode')

				if not _atco_code:
					return

				_new_data.setdefault(_atco_code, point)

				_naptan_code = point.get('NaptanCode', '')
				_name = point.get('Descriptor', {}).get('CommonName', '')
				_landmark = point.get('Landmark', '')
				_street = point.get('Street', '')
				_crossing = point.get('Crossing', '')
				_indicator = point.get('Descriptor', {}).get('Indicator', '')
				_locality_ref = point.get('Place', {}).get('NptgLocalityRef')
				_locality_name = ''

				if _locality_ref in _locality_list:
					# _locality_name = _locality_list[_locality_ref].get('Descriptor', {}).get('LocalityName', '')
					_locality_name = _locality_list[_locality_ref].get('name', {})

				_town = point.get('Place', {}).get('Town', '')
				_suburb = point.get('Place', {}).get('Suburb', '')
				_created_at = point.get('CreationDateTime', '')
				_modified_at = point.get('ModificationDateTime', '')
				_status = point.get('Status', '')
				_stop_category = ''
				_stop_class = point.get('StopClassification', {})
				_stop_type = _stop_class.get('StopType', '')
				_on_street = True if 'OnStreet' in _stop_class else False
				_sub_properties = {}

				# manual handling of irregular data
				if _stop_type in ['BCE', 'BST', 'BCS', 'BCQ'] and _on_street:
					_stop_type = 'BCT'

				match _stop_type:
					# Bus / Coach [Onstreet]
					case 'BCT':
						_stop_category = 'bus'
						_bus = _stop_class.get('OnStreet', {}).get('Bus', {})
						_timing_status = _bus.get('TimingStatus', '')
						_bus_stop_type = _bus.get('BusStopType', '')
						_bus_type = ''
						_coach_ref = _bus.get('AnnotatedCoachRef', {}) # to-do
						_bearing = ''
						_section = []

						match _bus_stop_type:
							case 'MKD':
								_bus_type = 'marked'
								_bearing = _bus.get('MarkedPoint', {}).get('Bearing', {}).get('CompassPoint', '')

							case 'CUS':
								_bus_type = 'custom'
								_bearing = _bus.get('UnmarkedPoint', {}).get('Bearing', {}).get('CompassPoint', '')

							case 'HAR':
								_bus_type = 'hailAndRide'

								if 'HailAndRideSection' in _bus:
									_start_point = _bus.get('HailAndRideSection', {}).get('StartPoint')
									_end_point = _bus.get('HailAndRideSection', {}).get('EndPoint')

									for _location in [_start_point, _end_point]:
										_location = _location.get('Translation', _location)

										if _location.get('Longitude') not in [None, '0.000000000'] and _location.get('Latitude') not in [None, '0.000000000']:
											_lon, _lat = _location.get('Longitude'), _location.get('Latitude')

										elif 'Easting' in _location and 'Northing' in _location:
											_lon, _lat = _transformer.transform(_location.get('Easting'),_location.get('Northing'))

										_section.append([float(_lon), float(_lat)])

								else:
									_bearing = _bus.get('MarkedPoint', {}).get('Bearing', {}).get('CompassPoint', '')

							case 'FLX':
								_bus_type = 'flexible'
								_locations = _bus.get('FlexibleZone', {}).get('Location', [])

								for _location in _locations:
									_location = _location.get('Translation', _location)

									if _location.get('Longitude') not in [None, '0.000000000'] and _location.get('Latitude') not in [None, '0.000000000']:
										_lon, _lat = _location.get('Longitude'), _location.get('Latitude')

									elif 'Easting' in _location and 'Northing' in _location:
										_lon, _lat = _transformer.transform(_location.get('Easting'),_location.get('Northing'))

									_section.append([float(_lon), float(_lat)])

						# if _bearing == '':
						# 	print(f"{_atco_code}: {_name} ({_stop_type}-{_bus_stop_type}) does not have compass point.")

						_sub_properties = {
							'type': _bus_type,
							'stopSubType': _bus_stop_type,
							'bearing': _bearing,
							'section': _section,
							'timingStatus': _timing_status,
							'annotatedCoachRef': _coach_ref
						}

					# Taxi [OnStreet]
					case 'TXR' | 'STR':
						_stop_category = 'taxi'
						_taxi = _stop_class.get('OnStreet', {}).get('Taxi', {})
						_taxi_type = toCamelCase(list(_taxi.keys())[0])

						_sub_properties = {
							'type': _taxi_type
						}

					# Car [OnStreet]
					case 'SDA':
						_stop_category = 'car'
						_car = _stop_class.get('OnStreet', {}).get('Car', {})
						_car_type = toCamelCase(list(_car.keys())[0])

						_sub_properties = {
							'type': _car_type
						}

					# Air [OffStreet]
					case 'AIR' | 'GAT':
						_stop_category = 'air'
						_air = _stop_class.get('OffStreet', {}).get('Air', {})
						_air_type = toCamelCase(list(_air.keys())[0])
						_air_ref = _air.get('AnnotatedAirRef', {}) # to-do

						_sub_properties = {
							'type': _air_type,
							'annotatedAirRef': _air_ref
						}

					# Ferry [OffStreet]
					case 'FTD' | 'FER' | 'FBT':
						_stop_category = 'ferry'
						_ferry = _stop_class.get('OffStreet', {}).get('Ferry', {})
						_ferry_type = toCamelCase(list(_ferry.keys())[0])
						_ferry_ref = _ferry.get('AnnotatedFerryRef', {}) # to-do

						_sub_properties = {
							'type': _ferry_type,
							'annotatedFerryRef': _ferry_ref
						}

					# Rail [OffStreet]
					case 'RSE' | 'RLY' | 'RPL':
						_stop_category = 'rail'
						_rail = _stop_class.get('OffStreet', {}).get('Rail', {})
						_rail_type = toCamelCase(list(_rail.keys())[0])
						_rail_ref = _rail.get('AnnotatedRailRef', {}) # to-do

						_sub_properties = {
							'type': _rail_type,
							'annotatedRailRef': _rail_ref
						}

					# Metro [OffStreet]
					case 'TMU' | 'MET' | 'PLT':
						_metro = _stop_class.get('OffStreet', {}).get('Metro', {})
						_rail = _stop_class.get('OffStreet', {}).get('Rail', {})

						if _metro:
							_stop_category = 'metro'
							_metro_type = toCamelCase(list(_metro.keys())[0])
							_metro_ref = _metro.get('AnnotatedMetroRef', {}) # to-do

							_sub_properties = {
								'type': _metro_type,
								'annotatedMetroRef': _metro_ref
							}

						elif _rail:
							_stop_category = 'rail'
							_rail_type = toCamelCase(list(_rail.keys())[0])
							_rail_ref = _rail.get('AnnotatedRailRef', {}) # to-do

							_sub_properties = {
								'type': _rail_type,
								'annotatedRailRef': _rail_ref
							}

						else:
							print(f"{_atco_code}: {_name} ({_stop_type}) is neither rail or metro.")

					# Telecabine (Lift & Cable Car) [OffStreet]
					case 'LCE' | 'LCB' | 'LPL':
						_stop_category = 'telecabine'
						_telecabine = _stop_class.get('OffStreet', {}).get('Telecabine', {})
						_telecabine_type = toCamelCase(list(_telecabine.keys())[0])
						_telecabine_ref = _telecabine.get('AnnotatedCablewayRef:', {}) # to-do

						_sub_properties = {
							'type': _telecabine_type,
							'AnnotatedCablewayRef': _telecabine_ref
						}

					# Bus / Coach [OffStreet]
					case 'BCE' | 'BST' | 'BCS' | 'BCQ':
						_stop_category = 'busAndCoach'
						_timing_status = ''
						_bus_coach = _stop_class.get('OffStreet', {}).get('BusAndCoach', {})
						_bus_coach_type = toCamelCase(list(_bus_coach.keys())[0])
						_type = _bus_coach.get(list(_bus_coach.keys())[0], {})

						if _type:
							_timing_statuses = _type.get('TimingStatus', '')

						_coach_ref = _bus_coach.get('AnnotatedCoachRef:', {}) # to-do

						_sub_properties = {
							'type': _bus_coach_type,
							'timingStatus': _timing_status,
							'annotatedCoachRef': _coach_ref
						}
				_stop_area_ref = point.get('StopAreas', {}).get('StopAreaRef', [])
				_stop_areas = []

				if isinstance(_stop_area_ref, str):
					_stop_areas.append(_stop_area_ref)

				elif not isinstance(_stop_area_ref, list):
					_stop_area_ref = [_stop_area_ref]

					for _a in _stop_area_ref:
						_stop_areas.append(_a.get('#text', ''))

				_admin_area_ref = point.get('AdministrativeAreaRef', '')
				_plusbuszone_ref = point.get('PlusbusZones', {}).get('PlusbusZoneRef', [])
				_plusbuszones = []

				if isinstance(_plusbuszone_ref, str):
					_plusbuszones.append(_plusbuszone_ref)

				elif not isinstance(_plusbuszone_ref, list):
					_plusbuszone_ref = [_plusbuszone_ref]

					for _z in _plusbuszone_ref:
						_plusbuszones.append(_z.get('#text', ''))

				_is_public = point.get('Public', 'true')
				_stop_validities = point.get('StopAvailability', {}).get('StopValidity', [])
				_validities = []

				if not isinstance(_stop_validities, list):
					_stop_validities = [_stop_validities]

				for _stop_validity in _stop_validities:
					_date_range = _stop_validity.get('DateRange', {})
					_validity_start_date = _date_range.get('StartDate', '')
					_validity_end_date = _date_range.get('EndDate', '')
					_validity_status = ''
					_transfer_to = ''
					_validity_note = _stop_validity.get('Note', '')

					match _stop_validity:
						case 'Active':
							_validity_status = 'active'

						case 'Suspended':
							_validity_status = 'suspended'

						case 'Transferred':
							_validity_status = 'transferred'
							_transfer_to = _stop_validity.get('Transferred', {}).get('StopPointRef', '')

					_validities.append({
						'validityStart': _validity_start_date,
						'validityEnd': _validity_end_date,
						'status': _validity_status,
						'transferToStop': _transfer_to,
						'remarks': _validity_note
					})

				_properties = {
					'atcoCode': _atco_code,
					'naptanCode': _naptan_code,
					'category': _stop_category,
					'stopType': _stop_type,
					'onStreet': _on_street,
					'name': _name,
					'landmark': _landmark,
					'street': _street,
					'crossing': _crossing,
					'locality': _locality_name,
					'town': _town,
					'suburb': _suburb,
					'coordinates': [float(_lon), float(_lat)],
					'indicator': _indicator,
					'properties': _sub_properties,
					'stopArea': _stop_areas,
					'adminArea': _admin_area_ref,
					'plusbusZone': _plusbuszones,
					'created': _created_at,
					'updated': _modified_at,
					'status': _status,
					'validity': _validities
				}

				_geodata['features'].append({
					'type': 'Feature',
					'geometry': {
						'type': 'Point',
						'coordinates': [float(_lon), float(_lat)]
					},
					'properties': _properties
				})

				_geodata_stops_all['features'].append({
					'type': 'Feature',
					'geometry': {
						'type': 'Point',
						'coordinates': [float(_lon), float(_lat)]
					},
					'properties': _properties
				})

				_stops_all.setdefault(_atco_code, _properties)

			_stop_points = _data.get('NaPTAN', {}).get('StopPoints', {}).get('StopPoint', [])

			if not isinstance(_stop_points, list):
				_stop_points = [_stop_points]

			for _stop_point in _stop_points:
				appendStopPoint(_stop_point)

			# with open(os.path.join(data_dir, f'naptan_stop_points_{atco}.json'), 'w') as f:
			# 	f.write(json.dumps(_new_data, ensure_ascii = False, separators=(',', ':')))
			# 	_len = len(_new_data)
			# 	print(f'Inserted {_len} stop points.')

			# with open(os.path.join(data_dir, f'naptan_stop_points_{atco}.geojson'), 'w') as f:
			# 	f.write(json.dumps(_geodata, ensure_ascii = False, separators=(',', ':')))
			# 	_len = len(_geodata['features'])
			# 	print(f'Inserted {_len} stop points in geo.')

			print('Creating GeoJSON for StopAreas ...')
			_new_data = {}

			_geodata = {
				'type': 'FeatureCollection',
				'features': []
			}

			def appendStopArea(point):
				global _stop_areas_all
				global _geodata_areas_all

				_location = point.get('Location', {})
				_location = _location.get('Translation', _location)

				if _location.get('Longitude') not in [None, '0.000000000'] and _location.get('Latitude') not in [None, '0.000000000']:
					_lon, _lat = _location.get('Longitude'), _location.get('Latitude')

				elif 'Easting' in _location and 'Northing' in _location:
					_lon, _lat = _transformer.transform(_location.get('Easting'),_location.get('Northing'))

				else:
					return

				_stop_area_code = point.get('StopAreaCode')

				if not _stop_area_code:
					return

				_new_data.setdefault(_stop_area_code, point)

				_parent_stop_area_ref = point.get('ParentStopAreaRef')
				_parent_stop_area_code = ''

				if isinstance(_parent_stop_area_ref, str):
					_parent_stop_area_code = _parent_stop_area_ref

				elif _parent_stop_area_ref:
					_parent_stop_area_code = _parent_stop_area_ref.get('#text', '')

				_name = point.get('Name', '')
				_admin_area_ref = point.get('AdministrativeAreaRef', '')
				_type = point.get('StopAreaType', '')
				_created_at = point.get('CreationDateTime', '')
				_modified_at = point.get('ModificationDateTime', '')

				_properties = {
					'stopAreaCode': _stop_area_code,
					'parent': _parent_stop_area_code,
					'name': _name,
					'adminArea': _admin_area_ref,
					'type': _type,
					'coordinates': [float(_lon), float(_lat)],
					'created': _created_at,
					'updated': _modified_at
				}

				_geodata['features'].append({
					'type': 'Feature',
					'geometry': {
						'type': 'Point',
						'coordinates': [float(_lon), float(_lat)]
					},
					'properties': _properties
				})

				_geodata_areas_all['features'].append({
					'type': 'Feature',
					'geometry': {
						'type': 'Point',
						'coordinates': [float(_lon), float(_lat)]
					},
					'properties': _properties
				})

				_stop_areas_all.setdefault(_stop_area_code, _properties)

			_stop_areas = _data.get('NaPTAN', {}).get('StopAreas', {}).get('StopArea', [])

			if not isinstance(_stop_areas, list):
				_stop_areas = [_stop_areas]

			for _stop_area in _stop_areas:
				appendStopArea(_stop_area)

			# with open(os.path.join(data_dir, f'naptan_stop_areas_{atco}.json'), 'w') as f:
			# 	f.write(json.dumps(_new_data, ensure_ascii = False, separators=(',', ':')))
			# 	_len = len(_new_data)
			# 	print(f'Inserted {_len} stop areas.')

			# with open(os.path.join(data_dir, f'naptan_stop_areas_{atco}.geojson'), 'w') as f:
			# 	f.write(json.dumps(_geodata, ensure_ascii = False, separators=(',', ':')))
			# 	_len = len(_geodata['features'])
			# 	print(f'Inserted {_len} stop areas in geo.')

		print('===')

def main():
	global _stops_all
	global _stop_areas_all
	global _geodata_stops_all
	global _geodata_areas_all

	_atco_list = getAtcoList()

	for _atco in _atco_list:
		getNaptan(_atco)

	os.makedirs(data_dir, exist_ok=True)
	# print('Creating aggregate JSON for StopPoints ...')
	# with open(os.path.join(data_dir, f'naptan_stop_points_all.json'), 'w') as f:
	# 	f.write(json.dumps(_stops_all, ensure_ascii = False, separators=(',', ':')))
	# 	_len = len(_stops_all)
	# 	print(f'Inserted {_len} stop points.')

	# print('Creating aggregate JSON for StopAreas ...')
	# with open(os.path.join(data_dir, f'naptan_stop_areas_all.json'), 'w') as f:
	# 	f.write(json.dumps(_stop_areas_all, ensure_ascii = False, separators=(',', ':')))
	# 	_len = len(_stop_areas_all)
	# 	print(f'Inserted {_len} stop areas.')

	# print('Creating aggregate GeoJSON for StopPoints ...')
	# with open(os.path.join(data_dir, f'naptan_stop_points_all.geojson'), 'w') as f:
	# 	f.write(json.dumps(_geodata_stops_all, ensure_ascii = False, separators=(',', ':')))
	# 	_len = len(_geodata_stops_all['features'])
	# 	print(f'Inserted {_len} stop points in geo.')

	# print('Creating aggregate GeoJSON for StopAreas ...')
	# with open(os.path.join(data_dir, f'naptan_stop_areas_all.geojson'), 'w') as f:
	# 	f.write(json.dumps(_geodata_areas_all, ensure_ascii = False, separators=(',', ':')))
	# 	_len = len(_geodata_areas_all['features'])
	# 	print(f'Inserted {_len} stop areas in geo.')

	print('Splitting StopPoints ...')
	os.makedirs(f'{data_dir}/stopPoints', exist_ok=True)
	for _k, _v in _stops_all.items():
		_d = {}
		_d[_k] = _v

		with open(os.path.join(f'{data_dir}/stopPoints', f'{_k}.json'), 'w') as f:
			f.write(json.dumps(_d, ensure_ascii = False, separators=(',', ':')))

	with open(os.path.join(f'{data_dir}', f'naptan_stop_points_all.json'), 'w') as f:
			f.write(json.dumps([_k for _k in _stops_all], ensure_ascii = False, separators=(',', ':')))

	print('Splitting StopAreas ...')
	os.makedirs(f'{data_dir}/stopAreas', exist_ok=True)
	for _k, _v in _stop_areas_all.items():
		_d = {}
		_d[_k] = _v

		with open(os.path.join(f'{data_dir}/stopAreas', f'{_k}.json'), 'w') as f:
			f.write(json.dumps(_d, ensure_ascii = False, separators=(',', ':')))

	with open(os.path.join(f'{data_dir}', f'naptan_stop_areas_all.json'), 'w') as f:
			f.write(json.dumps([_k for _k in _stop_areas_all], ensure_ascii = False, separators=(',', ':')))

if __name__ == "__main__":
	main()