import time
import os
import requests
import json
import xmltodict
import xml.etree.ElementTree as ET

data_dir = 'data/noc'

def retryRequest(url):
	while True:
		r = requests.get(url)

		if r.status_code == 200:
			return r

		elif r.status_code == 400:
			raise Exception(r.status_code, url)
			break

		elif r.status_code == 429:
			time.sleep(10)

		else:
			raise Exception(r.status_code, url)

def getNoc():
	def fetchNocData():
		try:
			_data = retryRequest('https://www.travelinedata.org.uk/noc/api/1.0/nocrecords.xml')

		except Exception:
			print('Cannot fetch NOC data. Retrying after 10 sec ...')
			time.sleep(10)
			fetchNocData()

		else:
			_parser = ET.XMLParser(encoding="iso-8859-1")
			_root = ET.fromstring(_data.content, parser=_parser)

			def sanitize(element):
				if element.text is not None:
					element.text = element.text.encode('unicode_escape').decode('utf-8')
				for child in element:
					sanitize(child)

			sanitize(_root)
			_data = ET.tostring(_root, encoding='utf-8').decode('utf-8')

			# with open(os.path.join(data_dir, f'noc.xml'), 'w') as f:
			# 	f.write(_data)

			return _data

	print('Getting NOC XML from API ...')
	_data = fetchNocData()

	print('Converting to JSON ...')
	_data = json.dumps(xmltodict.parse(_data), ensure_ascii = False, separators=(',', ':'))
	_data = _data.replace('@', '')

	os.makedirs(data_dir, exist_ok=True)
	with open(os.path.join(data_dir, 'noc.json'), 'w') as f:
		f.write(_data)

def main():
	getNoc()

if __name__ == "__main__":
	main()