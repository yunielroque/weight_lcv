def get_data():
	return {
		'fieldname': 'name',
		'non_standard_fieldnames': {
			'Journal Entry': 'linked_custom_lcv',
		},
		'transactions': [
			{
				'label': ['Billing Entry'],
				'items': ['Journal Entry']
			}
		]
	}