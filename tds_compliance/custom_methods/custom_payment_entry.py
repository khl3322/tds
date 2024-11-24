import frappe


def on_save(doc, method = None):
	if not doc.advance_payment_percentage:
		return

	if doc.advance_payment_percentage > 100:
		frappe.throw("Advance Payment Percentage cannot be more than 100")

	doc.tax_withholding_net_total = 0.0
	for row in doc.items:
		row.rate = row.price_list_rate * doc.advance_payment_percentage / 100
		row.amount = (row.rate * row.qty) - (row.tds_amount * doc.advance_payment_percentage / 100)

		doc.tax_withholding_net_total += row.amount

	doc.total = doc.tax_withholding_net_total
	doc.base_total = doc.tax_withholding_net_total
	doc.paid_amount = doc.tax_withholding_net_total

	for row in doc.references:
		row.allocated_amount = doc.paid_amount

	doc.total_allocated_amount = doc.paid_amount