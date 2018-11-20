# -*- coding: utf-8 -*-
# Copyright (c) 2018, Saif Ur Rehman and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe, erpnext
from frappe import _
from frappe.utils import flt
from frappe.model.meta import get_field_precision
from frappe.model.document import Document
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.accounts.party import get_party_account

class CustomLCV(Document):
	def get_items_from_purchase_receipts(self):
		self.set("items", [])
		self.total_amount = 0
		self.total_weight = 0
		for pr in self.get("purchase_receipts"):
			if pr.receipt_document_type and pr.receipt_document:
				pr_items = frappe.db.sql("""
					select item.item_code, item.description,
						item.qty, item.total_weight, item.weight_uom, item.base_rate, item.base_amount, item.name, item.cost_center
					from `tab{doctype} Item` item where parent = %s
					and exists(select name from tabItem where name = item.item_code and is_stock_item = 1)
					""".format(doctype=pr.receipt_document_type), pr.receipt_document, as_dict=True)

				for d in pr_items:
					item = self.append("items")
					item.item_code = d.item_code
					item.description = d.description
					item.qty = d.qty
					item.rate = d.base_rate
					item.cost_center = d.cost_center or erpnext.get_default_cost_center(self.company)
					item.amount = d.base_amount
					item.receipt_document_type = pr.receipt_document_type
					item.receipt_document = pr.receipt_document
					item.purchase_receipt_item = d.name

					item_master = frappe.db.get_value("Item", item.item_code, ["weight_uom", "weight_per_unit"], as_dict=1)
					item.weight = flt(item_master.weight_per_unit * d.qty, item.precision("weight"))
					item.weight_uom = item_master.weight_uom

					self.total_amount += item.amount
					self.total_weight += item.weight

	def validate(self):
		self.check_mandatory()
		self.validate_purchase_receipts()
		self.set_total_taxes_and_charges()
		if not self.get("items"):
			self.get_items_from_purchase_receipts()
		else:
			self.validate_applicable_charges_for_item()
		self.validate_weight_uoms()

	def validate_weight_uoms(self):
		if self.distribute_charges_based_on == "Weight":
			weight_uoms = set()
			for item in self.items:
				weight_uoms.add(str(item.weight_uom))
			if len(weight_uoms) > 1:
				frappe.throw(_("Weight UOMs of all items must be the same. UOMs: {0}").format(", ".join(weight_uoms)))

	def check_mandatory(self):
		if not self.get("purchase_receipts"):
			frappe.throw(_("Please enter Receipt Document"))

	def validate_purchase_receipts(self):
		receipt_documents = []

		for d in self.get("purchase_receipts"):
			if frappe.db.get_value(d.receipt_document_type, d.receipt_document, "docstatus") != 1:
				frappe.throw(_("Receipt document must be submitted"))
			else:
				receipt_documents.append(d.receipt_document)

		for item in self.get("items"):
			if not item.receipt_document:
				frappe.throw(_("Item must be added using 'Get Items from Purchase Receipts' button"))
			elif item.receipt_document not in receipt_documents:
				frappe.throw(_("Item Row {idx}: {doctype} {docname} does not exist in above '{doctype}' table")
					.format(idx=item.idx, doctype=item.receipt_document_type, docname=item.receipt_document))

			if not item.cost_center:
				frappe.throw(_("Row {0}: Cost center is required for an item {1}")
					.format(item.idx, item.item_code))

	def set_total_taxes_and_charges(self):
		self.total_taxes_and_charges = sum([flt(d.amount) for d in self.get("taxes")])

	def validate_applicable_charges_for_item(self):
		based_on = self.distribute_charges_based_on.lower()

		total = sum([flt(d.get(based_on)) for d in self.get("items")])

		if not total:
			frappe.throw(_("Total {0} for all items is zero, may be you should change 'Distribute Charges Based On'").format(based_on))

		total_applicable_charges = sum([flt(d.applicable_charges) for d in self.get("items")])

		precision = get_field_precision(frappe.get_meta("Landed Cost Item").get_field("applicable_charges"),
		currency=frappe.db.get_value("Company", self.company, "default_currency", cache=True))

		diff = flt(self.total_taxes_and_charges) - flt(total_applicable_charges)
		diff = flt(diff, precision)

		if abs(diff) < (2.0 / (10**precision)):
			self.items[-1].applicable_charges += diff
		else:
			frappe.throw(_("Total Applicable Charges in Purchase Receipt Items table must be same as Total Taxes and Charges"))

	def on_submit(self):
		self.update_landed_cost()
		self.make_payable_jv()

	def on_cancel(self):
		self.update_landed_cost()

	def make_payable_jv(self):
		jv = frappe.new_doc("Journal Entry")
		jv.linked_custom_lcv = self.name
		jv.company = self.company
		jv.posting_date = frappe.utils.nowdate()
		jv.append("accounts", {
			"account": self.expense_account,
			"debit": flt(self.total_taxes_and_charges),
			"debit_in_account_currency": flt(self.total_taxes_and_charges),
		})
		jv.append("accounts", {
			"account": get_party_account("Supplier", self.supplier, self.company),
			"party_type": "Supplier",
			"party": self.supplier,
			"credit": flt(self.total_taxes_and_charges),
			"credit_in_account_currency": flt(self.total_taxes_and_charges),
		})
		jv.insert()
		jv.submit()

	def update_landed_cost(self):
		for d in self.get("purchase_receipts"):
			doc = frappe.get_doc(d.receipt_document_type, d.receipt_document)

			# set landed cost voucher amount in pr item
			doc.set_landed_cost_voucher_amount()

			# set valuation amount in pr item
			doc.update_valuation_rate("items")

			# db_update will update and save landed_cost_voucher_amount and voucher_amount in PR
			for item in doc.get("items"):
				item.db_update()

			# update latest valuation rate in serial no
			self.update_rate_in_serial_no(doc)

			# update stock & gl entries for cancelled state of PR
			doc.docstatus = 2
			doc.update_stock_ledger(allow_negative_stock=True, via_landed_cost_voucher=True)
			doc.make_gl_entries_on_cancel(repost_future_gle=False)

			# update stock & gl entries for submit state of PR
			doc.docstatus = 1
			doc.update_stock_ledger(via_landed_cost_voucher=True)
			doc.make_gl_entries()

	def update_rate_in_serial_no(self, receipt_document):
		for item in receipt_document.get("items"):
			if item.serial_no:
				serial_nos = get_serial_nos(item.serial_no)
				if serial_nos:
					frappe.db.sql("update `tabSerial No` set purchase_rate=%s where name in ({0})"
						.format(", ".join(["%s"]*len(serial_nos))), tuple([item.valuation_rate] + serial_nos))
