import os
import json
import xmltodict
import time
import logging
import requests
from pyproj import Transformer

# Logger configuration
logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s [%(levelname)s] %(message)s',
	datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Data and URL configuration
naptan_dir = 'data/naptan'
atco_url = 'https://raw.githubusercontent.com/xavier114fch/uk-bus-open-data/refs/heads/gh-pages/data/nptg/nptg_atcoareas.json'
nptg_localities_url = 'https://raw.githubusercontent.com/xavier114fch/uk-bus-open-data/refs/heads/gh-pages/data/nptg/nptg_localities.json'

# Session configuration
session = requests.Session()
request_timeout = 30

# Transformer configuration
transformer = Transformer.from_crs(27700, 4326, always_xy=True)

# Global state containers
stops_all: dict = {}
stop_areas_all: dict = {}
geodata_stops_all: dict = {
	'type': 'FeatureCollection',
	'features': []
}
geodata_areas_all: dict = {
	'type': 'FeatureCollection',
	'features': []
}

# -------------------------------------------------------------------------
# Retry logic
# -------------------------------------------------------------------------

def retry_request(url: str, *, max_retries: int = 5, backoff_delay: int = 1):
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

# -------------------------------------------------------------------------
# Helper utilities
# -------------------------------------------------------------------------

def get_atco_list() -> list[str]:
	"""Return a sorted list of ATCO area codes from the NPTG area file."""
	resp = retry_request(atco_url)
	data = resp.json()
	return sorted(data.keys())

def to_camel_case(value: str) -> str:
	# Small helper to convert the first character to lower‑case.
	return value[0].lower() + value[1:]

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

# -------------------------------------------------------------------------
# Core functionality – fetching and building the data structures
# -------------------------------------------------------------------------

def fetch_nptg_localities() -> dict:
	"""Return the NPTG locality mapping from the public JSON resource."""
	resp = retry_request(nptg_localities_url)
	return resp.json()

def fetch_naptan_xml(atco: str) -> bytes:
	if atco != '900':
		url = f'https://naptan.api.dft.gov.uk/v1/access-nodes?atcoAreaCodes={atco}&dataFormat=xml'
		resp = retry_request(url)
		return resp.content
	else:
		logger.info('ATCO 900 is a special case representing all of Great Britain; Skipping fetch as it always returns HTTP 400.')
		return None

# Internal helpers that will mutate the global state.
# They operate on a *single* ATCO's stop list and stop area list.

def append_stop_point(point: dict, locality_list: dict):
	"""Process a single stop point record and add it to the global structures."""
	location = point.get('Place', {}).get('Location', {})
	location = location.get('Translation', location)

	if location.get('Longitude') not in [None, '0.000000000'] and location.get('Latitude') not in [None, '0.000000000']:
		lon, lat = location['Longitude'], location['Latitude']
	elif 'Easting' in location and 'Northing' in location:
		lon, lat = transformer.transform(location['Easting'], location['Northing'])
	else:
		return

	atco_code = point.get('AtcoCode')
	if not atco_code:
		return

	naptan_code = point.get('NaptanCode', '')
	name = point.get('Descriptor', {}).get('CommonName', '')
	landmark = point.get('Landmark', '')
	street = point.get('Street', '')
	crossing = point.get('Crossing', '')
	indicator = point.get('Descriptor', {}).get('Indicator', '')
	locality_ref = point.get('Place', {}).get('NptgLocalityRef')
	locality_name = ''

	if locality_ref in locality_list:
		locality_name = locality_list[locality_ref].get('name', {})

	town = point.get('Place', {}).get('Town', '')
	suburb = point.get('Place', {}).get('Suburb', '')
	created_at = point.get('CreationDateTime', '')
	modified_at = point.get('ModificationDateTime', '')
	status = point.get('Status', '')
	stop_category = ''
	stop_class = point.get('StopClassification', {})
	stop_type = stop_class.get('StopType', '')
	on_street = 'OnStreet' in stop_class
	sub_properties = {}
	admin_area_ref = point.get('AdministrativeAreaRef', '')

	if stop_type in ['BCE', 'BST', 'BCS', 'BCQ'] and on_street:
		stop_type = 'BCT'

	# ----- Construct the ``sub_properties`` field based on the ``StopType`` ----
	match stop_type:
		case 'BCT':
			stop_category = 'bus'
			bus = stop_class.get('OnStreet', {}).get('Bus', {})
			timing_status = bus.get('TimingStatus', '')
			bus_stop_type = bus.get('BusStopType', '')
			bus_type = ''
			coach_ref = bus.get('AnnotatedCoachRef', {})
			bearing = ''
			section = []
			match bus_stop_type:
				case 'MKD':
					bus_type = 'marked'
					bearing = bus.get('MarkedPoint', {}).get('Bearing', {}).get('CompassPoint', '')
				case 'CUS':
					bus_type = 'custom'
					bearing = bus.get('UnmarkedPoint', {}).get('Bearing', {}).get('CompassPoint', '')
				case 'HAR':
					bus_type = 'hailAndRide'
					section_part = bus.get('HailAndRideSection', {})
					for loc in [section_part.get('StartPoint'), section_part.get('EndPoint')]:
						if not loc:
							continue
						loc = loc.get('Translation', loc)
						if loc.get('Longitude') not in [None, '0.000000000'] and loc.get('Latitude') not in [None, '0.000000000']:
							har_lon, har_lat = loc['Longitude'], loc['Latitude']
						elif 'Easting' in loc and 'Northing' in loc:
							har_lon, har_lat = transformer.transform(loc['Easting'], loc['Northing'])
						else:
							continue
						section.append([float(har_lon), float(har_lat)])
				case 'FLX':
					flx_locations = bus.get('FlexibleZone', {}).get('Location', [])
					for loc in flx_locations:
						if not loc:
							continue
						loc = loc.get('Translation', loc)
						if loc.get('Longitude') not in [None, '0.000000000'] and loc.get('Latitude') not in [None, '0.000000000']:
							flx_lon, flx_lat = loc['Longitude'], loc['Latitude']
						elif 'Easting' in loc and 'Northing' in loc:
							flx_lon, flx_lat = transformer.transform(loc['Easting'], loc['Northing'])
						else:
							continue
						section.append([float(flx_lon), float(flx_lat)])
			sub_properties = {
				'type': bus_type,
				'stopSubType': bus_stop_type,
				'bearing': bearing,
				'section': section,
				'timingStatus': timing_status,
				'annotatedCoachRef': coach_ref,
			}
		case 'TXR' | 'STR':
			stop_category = 'taxi'
			taxi = stop_class.get('OnStreet', {}).get('Taxi', {})
			taxi_type = to_camel_case(list(taxi.keys())[0])
			sub_properties = {'type': taxi_type}
		case 'SDA':
			stop_category = 'car'
			car = stop_class.get('OnStreet', {}).get('Car', {})
			car_type = to_camel_case(list(car.keys())[0])
			sub_properties = {'type': car_type}
		case 'AIR' | 'GAT':
			stop_category = 'air'
			air = stop_class.get('OffStreet', {}).get('Air', {})
			air_type = to_camel_case(list(air.keys())[0])
			air_ref = air.get('AnnotatedAirRef', {})
			sub_properties = {
				'type': air_type,
				'annotatedAirRef': air_ref,
			}
		case 'FTD' | 'FER' | 'FBT':
			stop_category = 'ferry'
			ferry = stop_class.get('OffStreet', {}).get('Ferry', {})
			ferry_type = to_camel_case(list(ferry.keys())[0])
			ferry_ref = ferry.get('AnnotatedFerryRef', {})
			sub_properties = {
				'type': ferry_type,
				'annotatedFerryRef': ferry_ref,
			}
		case 'RSE' | 'RLY' | 'RPL':
			stop_category = 'rail'
			rail = stop_class.get('OffStreet', {}).get('Rail', {})
			rail_type = to_camel_case(list(rail.keys())[0])
			rail_ref = rail.get('AnnotatedRailRef', {})
			sub_properties = {
				'type': rail_type,
				'annotatedRailRef': rail_ref,
			}
		case 'TMU' | 'MET' | 'PLT':
			metro = stop_class.get('OffStreet', {}).get('Metro', {})
			rail = stop_class.get('OffStreet', {}).get('Rail', {})
			if metro:
				stop_category = 'metro'
				metro_type = to_camel_case(list(metro.keys())[0])
				metro_ref = metro.get('AnnotatedMetroRef', {})
				sub_properties = {
					'type': metro_type,
					'annotatedMetroRef': metro_ref,
				}
			elif rail:
				stop_category = 'rail'
				rail_type = to_camel_case(list(rail.keys())[0])
				rail_ref = rail.get('AnnotatedRailRef', {})
				sub_properties = {
					'type': rail_type,
					'annotatedRailRef': rail_ref,
				}
			else:
				logger.info(f"{atco_code}: {name} ({stop_type}) is neither rail or metro.")
		case 'LCE' | 'LCB' | 'LPL':
			stop_category = 'telecabine'
			tele = stop_class.get('OffStreet', {}).get('Telecabine', {})
			tele_type = to_camel_case(list(tele.keys())[0])
			tele_ref = tele.get('AnnotatedCablewayRef:', {})
			sub_properties = {
				'type': tele_type,
				'AnnotatedCablewayRef': tele_ref,
			}
		case 'BCE' | 'BST' | 'BCS' | 'BCQ':
			stop_category = 'busAndCoach'
			timing_status = ''
			bus_coach = stop_class.get('OffStreet', {}).get('BusAndCoach', {})
			bus_coach_type = to_camel_case(list(bus_coach.keys())[0])
			type_obj = bus_coach.get(list(bus_coach.keys())[0], {})
			if type_obj:
				timing_statuses = type_obj.get('TimingStatus', '')
			coach_ref = bus_coach.get('AnnotatedCoachRef:', {})
			sub_properties = {
				'type': bus_coach_type,
				'timingStatus': timing_status,
				'annotatedCoachRef': coach_ref,
			}

	# Collect area/zone references
	stop_areas = point.get('StopAreas', {}).get('StopAreaRef', [])
	if isinstance(stop_areas, str):
		stop_areas = [stop_areas]
	elif not isinstance(stop_areas, list):
		stop_areas = [stop_areas]
	stop_areas = [sa if isinstance(sa, str) else sa.get('#text', '') for sa in stop_areas]

	plusbuszones = point.get('PlusbusZones', {}).get('PlusbusZoneRef', [])
	if isinstance(plusbuszones, str):
		plusbuszones = [plusbuszones]
	elif not isinstance(plusbuszones, list):
		plusbuszones = [plusbuszones]
	plusbuszones = [z if isinstance(z, str) else z.get('#text', '') for z in plusbuszones]

	is_public = point.get('Public', False)
	stop_validities = point.get('StopAvailability', {}).get('StopValidity', [])
	validities = []

	if not isinstance(stop_validities, list):
		stop_validities = [stop_validities]

	for stop_validity in stop_validities:
		date_range = stop_validity.get('DateRange', {})
		validity_start_date = date_range.get('StartDate', '')
		validity_end_date = date_range.get('EndDate', '')
		validity_status = ''
		transfer_to = ''
		validity_note = stop_validity.get('Note', '')

		match stop_validity:
			case 'Active':
				validity_status = 'active'

			case 'Suspended':
				validity_status = 'suspended'

			case 'Transferred':
				validity_status = 'transferred'
				transfer_to = stop_validity.get('Transferred', {}).get('StopPointRef', '')

		validities.append({
			'validityStart': validity_start_date,
			'validityEnd': validity_end_date,
			'status': validity_status,
			'transferToStop': transfer_to,
			'remarks': validity_note
		})

	properties = {
		'atcoCode': atco_code,
		'naptanCode': naptan_code,
		'category': stop_category,
		'stopType': stop_type,
		'onStreet': on_street,
		'name': name,
		'landmark': landmark,
		'street': street,
		'crossing': crossing,
		'locality': locality_name,
		'town': town,
		'suburb': suburb,
		'coordinates': [float(lon), float(lat)],
		'indicator': indicator,
		'properties': sub_properties,
		'stopArea': stop_areas,
		'adminArea': admin_area_ref,
		'plusbusZone': plusbuszones,
		'created': created_at,
		'updated': modified_at,
		'status': status,
		'validity': validities,
		'isPublic': is_public
	}
	point_geom = {
		'type': 'Feature',
		'geometry': {'type': 'Point', 'coordinates': [float(lon), float(lat)]},
		'properties': properties,
	}

	stops_all.setdefault(atco_code, properties)
	geodata_stops_all['features'].append(point_geom)

def append_stop_area(point: dict):
	location = point.get('Location', {})
	location = location.get('Translation', location)
	if location.get('Longitude') not in [None, '0.000000000'] and location.get('Latitude') not in [None, '0.000000000']:
		lon, lat = location['Longitude'], location['Latitude']
	elif 'Easting' in location and 'Northing' in location:
		lon, lat = transformer.transform(location['Easting'], location['Northing'])
	else:
		return
	area_code = point.get('StopAreaCode')
	if not area_code:
		return
	parent_ref = point.get('ParentStopAreaRef')
	parent_code = ''
	if isinstance(parent_ref, str):
		parent_code = parent_ref
	elif parent_ref:
		parent_code = parent_ref.get('#text', '')
	name = point.get('Name', '')
	admin_area = point.get('AdministrativeAreaRef', '')
	stop_area_type = point.get('StopAreaType', '')
	created_at = point.get('CreationDateTime', '')
	modified_at = point.get('ModificationDateTime', '')
	properties = {
		'stopAreaCode': area_code,
		'parent': parent_code,
		'name': name,
		'adminArea': admin_area,
		'type': stop_area_type,
		'coordinates': [float(lon), float(lat)],
		'created': created_at,
		'updated': modified_at,
	}
	point_geom = {
		'type': 'Feature',
		'geometry': {'type': 'Point', 'coordinates': [float(lon), float(lat)]},
		'properties': properties,
	}

	stop_areas_all.setdefault(area_code, properties)
	geodata_areas_all['features'].append(point_geom)


# -------------------------------------------------------------------------

def get_naptan(atco: str, locality_map: dict) -> None:
	"""Populate the global containers with stop/area data for a single ATCO.

	The heavy lifting is performed in the helper functions defined
	above; they write directly to the module‑level dictionaries.
	"""
	logger.info(f'Getting NaPTAN XML of ATCO {atco} from API…')
	try:
		xml_content = fetch_naptan_xml(atco)
	except SystemExit:
		logger.error(f'Failed to fetch ATCO {atco}. Skipping.')
		return
	if xml_content is None:
		return
	logger.info('Converting XML to JSON …')
	xml_dict = xmltodict.parse(xml_content)
	data = clean_obj(xml_dict)

	# ---- StopPoints ----------------------------------------------------------------
	stop_points = data.get('NaPTAN', {}).get('StopPoints', {}).get('StopPoint', [])
	if not isinstance(stop_points, list):
		stop_points = [stop_points]

	for sp in stop_points:
		append_stop_point(sp, locality_map)

	logger.info(f'Inserted {len(stop_points)} stop points for ATCO {atco}.')

	# ---- StopAreas -----------------------------------------------------------------
	stop_areas = data.get('NaPTAN', {}).get('StopAreas', {}).get('StopArea', [])
	if not isinstance(stop_areas, list):
		stop_areas = [stop_areas]
	for pa in stop_areas:
		append_stop_area(pa)

	logger.info(f'Inserted {len(stop_areas)} stop areas for ATCO {atco}.')

# Helper for appending a single stop area – similar to the point logic.


# ``__main__`` section is intentionally lightweight
def main():
	atco_list = get_atco_list()
	locality_map = fetch_nptg_localities()

	for _atco in atco_list:
		get_naptan(_atco, locality_map)

	os.makedirs(naptan_dir, exist_ok=True)

	print('Splitting StopPoints ...')
	os.makedirs(f'{naptan_dir}/stopPoints', exist_ok=True)
	for k, v in stops_all.items():
		d = {}
		d[k] = v

		with open(os.path.join(f'{naptan_dir}/stopPoints', f'{k}.json'), 'w') as f:
			f.write(json.dumps(d, ensure_ascii = False, separators=(',', ':'), sort_keys=True))

	with open(os.path.join(f'{naptan_dir}', f'naptan_stop_points_all.json'), 'w') as f:
			f.write(json.dumps([k for k in stops_all], ensure_ascii = False, separators=(',', ':'), sort_keys=True))

	print('Splitting StopAreas ...')
	os.makedirs(f'{naptan_dir}/stopAreas', exist_ok=True)
	for k, v in stop_areas_all.items():
		d = {}
		d[k] = v


		with open(os.path.join(f'{naptan_dir}/stopAreas', f'{k}.json'), 'w') as f:
			f.write(json.dumps(d, ensure_ascii = False, separators=(',', ':'), sort_keys=True))

	with open(os.path.join(f'{naptan_dir}', f'naptan_stop_areas_all.json'), 'w') as f:
			f.write(json.dumps([k for k in stop_areas_all], ensure_ascii = False, separators=(',', ':'), sort_keys=True))

if __name__ == '__main__':
	main()