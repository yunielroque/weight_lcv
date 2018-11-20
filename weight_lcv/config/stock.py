from __future__ import unicode_literals
from frappe import _

def get_data():
	return [
		{
			"label": _("Tools"),
			"icon": "fa fa-wrench",
			"items": [
				{
					"type": "doctype",
					"name": "Custom LCV",
				}
			]
		}
	]