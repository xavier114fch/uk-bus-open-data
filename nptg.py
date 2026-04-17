import time
import os
import requests
import logging
import json
import xmltodict
from pyproj import Transformer

# Logger configuration
logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s [%(levelname)s] %(message)s',
	datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Data and URL configuration
data_dir = 'data/nptg'
nptg_url = 'https://naptan.api.dft.gov.uk/v1/nptg'

# Session configuration
session = requests.Session()
request_timeout = 30

# Transfomer configuration
transformer = Transformer.from_crs(27700, 4326, always_xy=True)

# Retry logic with exponential backoff for handling rate limits and transient errors
def retry_request(url, max_retries=5, backoff_delay=1):
	for attempt in range(max_retries):
		try:
			resp = session.get(url, timeout=request_timeout)
			if resp.status_code == 200:
				return resp
			if resp.status_code == 429:
				logger.warning(f'Rate limited (429). Waiting {backoff_delay}s before retry...')
				time.sleep(backoff_delay)
				backoff_delay *= 2
				continue
			resp.raise_for_status()
		except requests.RequestException as exc:
			logger.error(f'Request exception: {exc}. Retrying...')
			time.sleep(backoff_delay)
			backoff_delay *= 2
	raise SystemExit(f'Failed to fetch {url} after {max_retries} attempts.')

def get_nptg():
	def fetch_nptg_data():
		data = retry_request(nptg_url)
		os.makedirs(data_dir, exist_ok=True)
		with open(os.path.join(data_dir, 'nptg.xml'), 'wb') as f:
			f.write(data.content)

		return data.content

	logger.info('Getting NPTG XML from API ...')
	data = fetch_nptg_data()

	logger.info('Converting to JSON ...')
	# Parse XML to dict and clean attribute prefixes

	def clean_obj(obj):
		if isinstance(obj, dict):
			# Special case: {"@xml:lang": "en", "#text": "Name"}
			if set(obj.keys()) == {"@xml:lang", "#text"}:
				return obj["#text"]
			return {k.lstrip('@'): clean_obj(v) for k, v in obj.items()}
		elif isinstance(obj, list):
			return [clean_obj(v) for v in obj]
		else:
			return obj

	parsed = xmltodict.parse(data)
	cleaned = clean_obj(parsed)
	data = json.dumps(cleaned, ensure_ascii=False, separators=(',', ':'), sort_keys=True)

	with open(os.path.join(data_dir, 'nptg.json'), 'w') as f:
		f.write(data)

	data = json.loads(data)

	logger.info('Creating JSON for Regions ...')
	new_data, atco_data = {}, {}

	def append_regions(point):
		new_data.setdefault(point['RegionCode'], point)

		admin_areas = point.get('AdministrativeAreas', {}).get('AdministrativeArea', [])
		if not isinstance(admin_areas, list):
			admin_areas = [admin_areas]

		for admin_area in admin_areas:
			atco_data.setdefault(admin_area['AtcoAreaCode'], admin_area)

	for point in data.get('NationalPublicTransportGazetteer', {}).get('Regions', {}).get('Region', []):
		append_regions(point)

	with open(os.path.join(data_dir, 'nptg_regions.json'), 'w') as f:
		f.write(json.dumps(new_data, ensure_ascii = False, separators=(',', ':'), sort_keys=True))

	with open(os.path.join(data_dir, 'nptg_atcoareas.json'), 'w') as f:
		f.write(json.dumps(atco_data, ensure_ascii = False, separators=(',', ':'), sort_keys=True))

	logger.info('Creating JSON for Localities ...')
	new_data, revised_data = {}, {}

	geodata = {
		'type': 'FeatureCollection',
		'features': []
	}

	def append_localities(point):
		location = point.get('Location', {})
		translation = location.get('Translation', {})

		if translation.get('Longitude') not in [None, '0.000000000'] and translation.get('Latitude') not in [None, '0.000000000']:
			try:
				lon = float(translation.get('Longitude'))
				lat = float(translation.get('Latitude'))
			except (TypeError, ValueError):
				return
		elif location.get('Easting') is not None and location.get('Northing') is not None:
			lon, lat = transformer.transform(location.get('Easting'), location.get('Northing'))
		else:
			return

		nptg_locality_code = point.get('NptgLocalityCode')

		if not nptg_locality_code:
			return

		new_data.setdefault(nptg_locality_code, point)

		name = point.get('Descriptor', {}).get('LocalityName', '')
		qualifier = point.get('Descriptor', {}).get('Qualify', {}).get('QualifierName')

		if qualifier:
			name = f'{name}, {qualifier}'

		alt_name = point.get('AlternativeDescriptors', {}).get('Descriptor', {}).get('LocalityName')

		revised_data.setdefault(nptg_locality_code, {
			'name': name,
			'altName': alt_name,
			'adminArea': point.get('AdministrativeAreaRef'),
			'nptgDistrict': point.get('NptgDistrictRef'),
			'sourceType': point.get('SourceLocalityType'),
			'classification': point.get('LocalityClassification'),
			'parent': point.get('ParentNptgLocalityRef', {}).get('#text'),
			'coords': [float(lon), float(lat)]
		})

		geodata['features'].append({
			'type': 'Feature',
			'geometry': {
				'type': 'Point',
				'coordinates': [float(lon), float(lat)]
			},
			'properties': {
				'nptgLocalityCode': nptg_locality_code,
				'name': name
			}
		})

	for point in data.get('NationalPublicTransportGazetteer', {}).get('NptgLocalities', {}).get('NptgLocality', []):
		append_localities(point)

	for k, v in revised_data.items():
		if 'parent' in v and v['parent'] in revised_data.keys():
			if 'children' not in revised_data[v['parent']]:
				revised_data[v['parent']]['children'] = []

			revised_data[v['parent']]['children'].append(k)

	# with open(os.path.join(data_dir, f'nptg_localities.json'), 'w') as f:
	# 	f.write(json.dumps(new_data, ensure_ascii = False, separators=(',', ':'), sort_keys=True))

	with open(os.path.join(data_dir, 'nptg_localities.geojson'), 'w') as f:
		f.write(json.dumps(geodata, ensure_ascii = False, separators=(',', ':'), sort_keys=True))

	with open(os.path.join(data_dir, 'nptg_localities.json'), 'w') as f:
		f.write(json.dumps(revised_data, ensure_ascii = False, separators=(',', ':'), sort_keys=True))

	for k, v in revised_data.items():
		d = {}
		d[k] = v

		os.makedirs(f'{data_dir}/localities', exist_ok=True)
		with open(os.path.join(f'{data_dir}/localities', f'{k}.json'), 'w') as f:
			f.write(json.dumps(d, ensure_ascii = False, separators=(',', ':'), sort_keys=True))

	logger.info('Creating JSON for PlusbusZones ...')
	new_data, revised_data = {}, {}

	geodata = {
		'type': 'FeatureCollection',
		'features': []
	}

	def append_plusbuszones(point):
		locs = []
		locations = point.get('Mapping', {}).get('Location', [])

		for location in locations:
			lon, lat = transformer.transform(location.get('Easting'), location.get('Northing'))
			locs.append([float(lon), float(lat)])

		code = point.get('PlusbusZoneCode')

		if not code:
			return

		new_data.setdefault(code, point)

		revised_data.setdefault(code, {
			'name': point.get('Name'),
			'country': point.get('Country'),
			'locations': [locs]
		})

		geodata['features'].append({
			'type': 'Feature',
			'geometry': {
				'type': 'Polygon',
				'coordinates': [locs]
			},
			'properties': {
				'plusbusZoneCode': code,
				'name': point.get('Name')
			}
		})

	for point in data.get('NationalPublicTransportGazetteer', {}).get('PlusbusZones', {}).get('PlusbusZone', []):
		append_plusbuszones(point)

	# with open(os.path.join(data_dir, f'nptg_plusbuszones.json'), 'w') as f:
	# 	f.write(json.dumps(new_data, ensure_ascii = False, separators=(',', ':'), sort_keys=True))

	with open(os.path.join(data_dir, 'nptg_plusbuszones.geojson'), 'w') as f:
		f.write(json.dumps(geodata, ensure_ascii = False, separators=(',', ':'), sort_keys=True))

	with open(os.path.join(data_dir, 'nptg_plusbuszones.json'), 'w') as f:
		f.write(json.dumps(revised_data, ensure_ascii = False, separators=(',', ':'), sort_keys=True))


	for k, v in revised_data.items():
		d = {}
		d[k] = v

		os.makedirs(f'{data_dir}/plusbuszones', exist_ok=True)
		with open(os.path.join(f'{data_dir}/plusbuszones', f'{k}.json'), 'w') as f:
			f.write(json.dumps(d, ensure_ascii = False, separators=(',', ':'), sort_keys=True))

def main():
	get_nptg()

if __name__ == "__main__":
	main()
