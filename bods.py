import time
import os
import requests
import json
import xmltodict

data_dir = 'data/bods'

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

def fetchTfLRef():
	_api_key = os.environ.get('TFL_API_KEY')

	# Test env variables
	if not _api_key:
		raise RuntimeError('Missing TfL API key from env variables.')

	def fetchBods(_api_key):
		try:
			_data = retryRequest(f'https://data.bus-data.dft.gov.uk/api/v1/datafeed/?operatorRef=TFLO&api_key={_api_key}')

		except Exception as err:
			print(f'Cannot fetch TFLO lineRef data from BODS. {err}')
			time.sleep(10)
			fetchBods(_api_key)
			return None

		else:
			os.makedirs(data_dir, exist_ok=True)
			with open(os.path.join(data_dir, 'bods_tflo_location_data.xml'), 'wb') as f:
				f.write(_data.content)

			return _data.content

	print('Getting TFLO lineRef from BODS API ...')
	_data = fetchBods(_api_key)

	if _data is not None:
		print('Converting to JSON ...')
		_data = json.dumps(xmltodict.parse(_data), ensure_ascii = False, separators=(',', ':'))

		os.makedirs(data_dir, exist_ok=True)
		with open(os.path.join(data_dir, 'bods_tflo_location_data.json'), 'w') as f:
			f.write(_data)

def outputTflRef():
	try:
		with open(os.path.join(data_dir, 'bods_tflo_location_data.json'), 'r') as f:
			_data = json.load(f)

	except BaseException:
		print('Cannot open BODS TFLO location data.')

	else:
		if os.path.exists(os.path.join(data_dir, 'bods_tflo_lineRef_mapping.json')):
			with open(os.path.join(data_dir, 'bods_tflo_lineRef_mapping.json'), 'r') as f:
				_mapping = json.load(f)

		else:
			_mapping = {}

		print('Mapping BODS lineRef with TfL route numbers ...')
		if 'Siri' in _data:
			if 'ServiceDelivery' in _data['Siri']:
				if 'VehicleMonitoringDelivery' in _data['Siri']['ServiceDelivery']:
					if 'VehicleActivity' in _data['Siri']['ServiceDelivery']['VehicleMonitoringDelivery']:
						_vehicle_activity_list = _data['Siri']['ServiceDelivery']['VehicleMonitoringDelivery']['VehicleActivity']

						if not isinstance(_vehicle_activity_list, list):
							_vehicle_activity_list = [_vehicle_activity_list]

						for _activity in _vehicle_activity_list:
							if 'MonitoredVehicleJourney' in _activity:
								_line_ref = _activity['MonitoredVehicleJourney']['LineRef']
								_published_line_name = _activity['MonitoredVehicleJourney']['PublishedLineName']

								if 'OriginRef' in _activity['MonitoredVehicleJourney']:
									_origin = _activity['MonitoredVehicleJourney']['OriginRef']

								else:
									_origin = ''

								if 'DestinationRef' in _activity['MonitoredVehicleJourney']:
									_dest = _activity['MonitoredVehicleJourney']['DestinationRef']

								else:
									_dest = ''

								if _published_line_name not in _mapping:
									_mapping[_published_line_name] = {}

								if _line_ref not in _mapping[_published_line_name]:
									_mapping[_published_line_name][_line_ref] = []

								if [_origin, _dest] not in _mapping[_published_line_name][_line_ref]:
									_mapping[_published_line_name][_line_ref].append([_origin, _dest])

						os.makedirs(data_dir, exist_ok=True)
						with open(os.path.join(data_dir, 'bods_tflo_lineRef_mapping.json'), 'w') as f:
							f.write(json.dumps(_mapping, ensure_ascii = False, separators=(',', ':')))


def main():
	fetchTfLRef()
	outputTflRef()

if __name__ == "__main__":
	main()