import json
from functools import reduce

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

@frappe.whitelist()
def custom_get_payment_entry(
	dt,
	dn,
	party_amount=None,
	bank_account=None,
	bank_amount=None,
	party_type=None,
	payment_type=None,
	reference_date=None,
	ignore_permissions=False,
	created_from_payment_request=False,
):
	pe = get_payment_entry(
		dt,
		dn,
		party_amount=party_amount,
		bank_account=bank_account,
		bank_amount=bank_amount,
		party_type=party_type,
		payment_type=payment_type,
		reference_date=reference_date,
		ignore_permissions=ignore_permissions,
		created_from_payment_request=created_from_payment_request,
	)

	po_doc = frappe.get_doc(dt, dn)
	fields_tp_copy = [
		"item_code", "item_name", "description", "uom", "stock_uom", "qty", "rate", "amount", "conversion_factor", "base_rate", "base_amount", "net_rate", "net_amount", "apply_tds", "custom_tax_withholding_category", "tds_amount"
	]


	for row in po_doc.items:
		args = {}
		for field in fields_tp_copy:
			args[field] = row.get(field)

		args["price_list_rate"] = row.rate
		pe.append("items", args)

	outstanding_amt = 0.0
	advance_payment_percentage = 100
	if po_doc.advance_paid:
		outstanding_amt = po_doc.rounded_total - po_doc.advance_paid
		advance_payment_percentage = outstanding_amt * 100 / po_doc.rounded_total

	pe.advance_payment_percentage = advance_payment_percentage
	pe.currency = po_doc.currency

	return pe