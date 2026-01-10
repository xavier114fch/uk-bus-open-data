import time
import os
import requests
import json
import xmltodict
import re
from pyproj import Transformer

data_dir = 'data/nptg'

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

def getNptg():
	def fetchNptgData():
		try:
			_data = retryRequest('https://naptan.api.dft.gov.uk/v1/nptg')

		except Exception:
			print('Cannot fetch NPTG data. Retrying after 10 sec ...')
			time.sleep(10)
			fetchNptgData()

		else:
			os.makedirs(data_dir, exist_ok=True)
			with open(os.path.join(data_dir, 'nptg.xml'), 'wb') as f:
				f.write(_data.content)

			return _data.content

	print('Getting NPTG XML from API ...')
	_data = fetchNptgData()

	print('Converting to JSON ...')
	_data = json.dumps(xmltodict.parse(_data), ensure_ascii = False, separators=(',', ':'))
	_pattern = r'{(?:\'|")@xml:lang(?:\'|"):(?:\'|")([A-Za-z]{2})(?:\'|"),(?:\'|")#text(?:\'|"):(?:\'|")(.*?)(?:\'|")}'
	_data = re.sub(_pattern, r'"\2"', _data)
	_data = _data.replace('@', '')

	with open(os.path.join(data_dir, 'nptg.json'), 'w') as f:
		f.write(_data)

	_data = json.loads(_data)

	print('Creating JSON for Regions ...')
	_new_data, _atco_data = {}, {}

	def appendRegions(point):
		_new_data.setdefault(point['RegionCode'], point)

		_admin_areas = point.get('AdministrativeAreas', {}).get('AdministrativeArea', [])
		if not isinstance(_admin_areas, list):
			_admin_areas = [_admin_areas]

		for _admin_area in _admin_areas:
			_atco_data.setdefault(_admin_area['AtcoAreaCode'], _admin_area)

	for _point in _data.get('NationalPublicTransportGazetteer', {}).get('Regions', {}).get('Region', []):
		appendRegions(_point)

	with open(os.path.join(data_dir, 'nptg_regions.json'), 'w') as f:
		f.write(json.dumps(_new_data, ensure_ascii = False, separators=(',', ':')))

	with open(os.path.join(data_dir, 'nptg_atcoareas.json'), 'w') as f:
		f.write(json.dumps(_atco_data, ensure_ascii = False, separators=(',', ':')))

	print('Creating JSON for Localities ...')
	_new_data, _revised_data = {}, {}

	_geodata = {
		'type': 'FeatureCollection',
		'features': []
	}

	def appendLocalities(point):
		_location = point.get('Location', {})
		_translation = _location.get('Translation', {})
		_transformer = Transformer.from_crs(27700, 4326, always_xy=True)

		if _translation.get('Longitude') not in [None, '0.000000000'] and _translation.get('Latitude') not in [None, '0.000000000']:
			_lon, _lat = _translation.get('Longitude'), _translation.get('Latitude')

		elif 'Easting' in _location and 'Northing' in _location:
			_lon, _lat = _transformer.transform(_location.get('Easting'),_location.get('Northing'))

		else:
			return

		_nptg_locality_code = point.get('NptgLocalityCode')

		if not _nptg_locality_code:
			return

		_new_data.setdefault(_nptg_locality_code, point)

		_name = point.get('Descriptor', {}).get('LocalityName', '')
		_qualifier = point.get('Descriptor', {}).get('Qualify', {}).get('QualifierName')

		if _qualifier:
			_name = f'{_name}, {_qualifier}'

		_alt_name = point.get('AlternativeDescriptors', {}).get('Descriptor', {}).get('LocalityName')

		_revised_data.setdefault(_nptg_locality_code, {
			'name': _name,
			'altName': _alt_name,
			'adminArea': point.get('AdministrativeAreaRef'),
			'nptgDistrict': point.get('NptgDistrictRef'),
			'sourceType': point.get('SourceLocalityType'),
			'classification': point.get('LocalityClassification'),
			'parent': point.get('ParentNptgLocalityRef', {}).get('#text'),
			'coords': [float(_lon), float(_lat)]
		})

		_geodata['features'].append({
			'type': 'Feature',
			'geometry': {
				'type': 'Point',
				'coordinates': [float(_lon), float(_lat)]
			},
			'properties': {
				'nptgLocalityCode': _nptg_locality_code,
				'name': _name
			}
		})

	for _point in _data.get('NationalPublicTransportGazetteer', {}).get('NptgLocalities', {}).get('NptgLocality', []):
		appendLocalities(_point)

	for _k, _v in _revised_data.items():
		if 'parent' in _v and _v['parent'] in _revised_data.keys():
			if 'children' not in _revised_data[_v['parent']]:
				_revised_data[_v['parent']]['children'] = []

			_revised_data[_v['parent']]['children'].append(_k)

	# with open(os.path.join(data_dir, f'nptg_localities.json'), 'w') as f:
	# 	f.write(json.dumps(_new_data, ensure_ascii = False, separators=(',', ':')))

	with open(os.path.join(data_dir, 'nptg_localities.geojson'), 'w') as f:
		f.write(json.dumps(_geodata, ensure_ascii = False, separators=(',', ':')))

	with open(os.path.join(data_dir, 'nptg_localities.json'), 'w') as f:
		f.write(json.dumps(_revised_data, ensure_ascii = False, separators=(',', ':')))

	for _k, _v in _revised_data.items():
		_d = {}
		_d[_k] = _v

		os.makedirs(f'{data_dir}/localities', exist_ok=True)
		with open(os.path.join(f'{data_dir}/localities', f'{_k}.json'), 'w') as f:
			f.write(json.dumps(_d, ensure_ascii = False, separators=(',', ':')))

	print('Creating JSON for PlusbusZones ...')
	_new_data, _revised_data = {}, {}

	_geodata = {
		'type': 'FeatureCollection',
		'features': []
	}

	def appendPlusbusZones(point):
		_locs = []
		_locations = point.get('Mapping', {}).get('Location', [])
		_transformer = Transformer.from_crs(27700, 4326, always_xy=True)

		for _location in _locations:
			_lon, _lat = _transformer.transform(_location.get('Easting'), _location.get('Northing'))
			_locs.append([float(_lon), float(_lat)])

		_code = point.get('PlusbusZoneCode')

		if not _code:
			return

		_new_data.setdefault(_code, point)

		_revised_data.setdefault(_code, {
			'name': point.get('Name'),
			'country': point.get('Country'),
			'locations': [_locs]
		})

		_geodata['features'].append({
			'type': 'Feature',
			'geometry': {
				'type': 'Polygon',
				'coordinates': [_locs]
			},
			'properties': {
				'plusbusZoneCode': _code,
				'name': point.get('Name')
			}
		})

	for _point in _data.get('NationalPublicTransportGazetteer', {}).get('PlusbusZones', {}).get('PlusbusZone', []):
		appendPlusbusZones(_point)

	# with open(os.path.join(data_dir, f'nptg_plusbuszones.json'), 'w') as f:
	# 	f.write(json.dumps(_new_data, ensure_ascii = False, separators=(',', ':')))

	with open(os.path.join(data_dir, 'nptg_plusbuszones.geojson'), 'w') as f:
		f.write(json.dumps(_geodata, ensure_ascii = False, separators=(',', ':')))

	with open(os.path.join(data_dir, 'nptg_plusbuszones.json'), 'w') as f:
		f.write(json.dumps(_revised_data, ensure_ascii = False, separators=(',', ':')))

	for _k, _v in _revised_data.items():
		_d = {}
		_d[_k] = _v

		os.makedirs(f'{data_dir}/plusbuszones', exist_ok=True)
		with open(os.path.join(f'{data_dir}/plusbuszones', f'{_k}.json'), 'w') as f:
			f.write(json.dumps(_d, ensure_ascii = False, separators=(',', ':')))

def main():
	getNptg()

if __name__ == "__main__":
	main()