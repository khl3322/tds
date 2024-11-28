import frappe
import erpnext

from frappe import _, qb
from frappe.query_builder import Criterion
from frappe.query_builder.functions import Abs, Sum
from frappe.utils import cint, flt, getdate
from erpnext.accounts.doctype.tax_withholding_category.tax_withholding_category import get_lower_deduction_certificate, get_cost_center

from erpnext.controllers.accounts_controller import validate_account_head


from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice

class CustomPurchaseInvoice(PurchaseInvoice):
	def allocate_advance_tds(self, tax_withholding_details, advance_taxes):
		for tax_account, tax_details in tax_withholding_details.items():
			tax_holding_amount = tax_withholding_details.get(tax_account).get("tax_amount")

			for tax in advance_taxes:
				allocated_amount = 0
				pending_amount = flt(tax.tax_amount - tax.allocated_amount)
				if flt(tax_details.get("tax_amount")) >= pending_amount:
					tax_details["tax_amount"] -= pending_amount
					allocated_amount = pending_amount
				elif (
					flt(tax_details.get("tax_amount"))
					and flt(tax_details.get("tax_amount")) < pending_amount
				):
					allocated_amount = tax_details["tax_amount"]
					allocate_advance_tds["tax_amount"] = 0

				self.append(
					"advance_tax",
					{
						"reference_type": "Payment Entry",
						"reference_name": tax.parent,
						"reference_detail": tax.name,
						"account_head": tax_account,
						"allocated_amount": allocated_amount,
					},
				)


	def set_tax_withholding(self):
		self.set("advance_tax", [])
		self.set("tax_withheld_vouchers", [])

		tax_withholding_details = {}
		for row in self.items:
			if not row.custom_tax_withholding_category:
				continue

			_tax_wh_details, advance_taxes, voucher_wise_amount = get_party_tax_withholding_details(
				self, row, row.custom_tax_withholding_category
			)

			if _tax_wh_details.get("account_head") not in tax_withholding_details:
				tax_withholding_details[_tax_wh_details.get("account_head")] = _tax_wh_details
			else:
				tax_withholding_details[_tax_wh_details.get("account_head")]["tax_amount"] += _tax_wh_details.get("tax_amount")

		# Adjust TDS paid on advances
		self.allocate_advance_tds(tax_withholding_details, advance_taxes)

		if not tax_withholding_details:
			return

		accounts = []
		for d in self.taxes:
			if tax_withholding_details.get(d.account_head):
				d.update(tax_withholding_details.get(d.account_head))

			accounts.append(d.account_head)

		for account, tax_details in tax_withholding_details.items():
			if not accounts or tax_details.get("account_head") not in accounts:
				self.append("taxes", tax_details)

		to_remove = [
			d
			for d in self.taxes
			if not d.tax_amount and tax_withholding_details.get(d.account_head)
		]

		for d in to_remove:
			self.remove(d)

		## Add pending vouchers on which tax was withheld
		for voucher_no, voucher_details in voucher_wise_amount.items():
			self.append(
				"tax_withheld_vouchers",
				{
					"voucher_name": voucher_no,
					"voucher_type": voucher_details.get("voucher_type"),
					"taxable_amount": voucher_details.get("amount"),
				},
			)

		# calculate totals again after applying TDS
		self.calculate_taxes_and_totals()


def validate_purchase_invoice(doc, method=None):
	apply_tds(doc)

def apply_tds(doc):

	tax_holding_details = frappe._dict({})

	for row in doc.items:
		if not row.custom_tax_withholding_category:
			continue

		if not row.apply_tds:
			continue

		voucher_wise_amount = {}
		advance_taxes = []

		if doc.doctype == "Purchase Invoice":
			tax_withholding_details, advance_taxes, voucher_wise_amount = get_party_tax_withholding_details(
				doc, row, row.custom_tax_withholding_category
			)
		else:
			tax_withholding_details = get_party_tax_withholding_details(doc, row, row.custom_tax_withholding_category)

		if advance_taxes:
			# Adjust TDS paid on advances
			doc.allocate_advance_tds(tax_withholding_details, advance_taxes)

		if not tax_withholding_details:
			continue

		accounts = []
		for d in doc.taxes:
			if d.account_head == tax_withholding_details.get("account_head"):
				d.update(tax_withholding_details)

			accounts.append(d.account_head)

		if not accounts or tax_withholding_details.get("account_head") not in accounts:
			doc.append("taxes", tax_withholding_details)

		to_remove = [
			d
			for d in doc.taxes
			if not d.rate and d.account_head == tax_withholding_details.get("account_head")
		]

		for d in to_remove:
			doc.remove(d)

		if tax_withholding_details.get("account_head") not in tax_holding_details:
			tax_holding_details.setdefault(tax_withholding_details.get("account_head"), tax_withholding_details)
		elif tax_withholding_details.get("account_head") in tax_holding_details:
			tax_holding_details[tax_withholding_details.get("account_head")]["tax_amount"] += tax_withholding_details.get("tax_amount")


		## Add pending vouchers on which tax was withheld
		for voucher_no, voucher_details in voucher_wise_amount.items():
			doc.append(
				"tax_withheld_vouchers",
				{
					"voucher_name": voucher_no,
					"voucher_type": voucher_details.get("voucher_type"),
					"taxable_amount": voucher_details.get("amount"),
				},
			)

	if not tax_holding_details:
		return

	for account_head, tax_details in tax_holding_details.items():
		if not tax_details.get("tax_amount"):
			continue

		doc.append("taxes", tax_details)

		# calculate totals again after applying TDS

	if doc.doctype != "Payment Entry":
		doc.calculate_taxes_and_totals()


def get_party_tax_withholding_details(inv, inv_row, tax_withholding_category=None):
	parties = []
	party_type, party = get_party_details(inv)
	pan_no = frappe.get_cached_value(party_type, party, "custom_pan_number")
	is_adhar_compliance = frappe.get_cached_value(party_type, party, "custom_is_aadhar_link_compliance")
	if not parties:
		parties.append(party)

	posting_date = inv.get("posting_date") or inv.get("transaction_date")
	tax_details = get_tax_withholding_details(tax_withholding_category, posting_date, inv.company, pan_no, is_adhar_compliance)

	tax_amount, tax_deducted, tax_deducted_on_advances, voucher_wise_amount = get_tax_amount(
		party_type, parties, inv, inv_row, tax_details, posting_date, pan_no
	)

	if inv.doctype == "Purchase Order":
		inv_row.tds_amount = tax_amount

	if party_type == "Supplier":
		tax_row = get_tax_row_for_tds(tax_details, tax_amount)
	else:
		tax_row = get_tax_row_for_tcs(inv, tax_details, tax_amount, tax_deducted)

	cost_center = get_cost_center(inv)
	tax_row.update(
		{
			"cost_center": cost_center,
			"is_tax_withholding_account": 1,
		}
	)

	if inv.doctype == "Purchase Invoice":
		return tax_row, tax_deducted_on_advances, voucher_wise_amount
	else:
		return tax_row

def get_tax_row_for_tds(tax_details, tax_amount):
	return {
		"category": "Total",
		"charge_type": "Actual",
		"tax_amount": tax_amount,
		"add_deduct_tax": "Deduct",
		"description": tax_details.description,
		"account_head": tax_details.account_head,
	}


def get_tax_withholding_rates(tax_withholding, posting_date):
	# returns the row that matches with the fiscal year from posting date
	for rate in tax_withholding.rates:
		if getdate(rate.from_date) <= getdate(posting_date) <= getdate(rate.to_date):
			return rate

	frappe.throw(_("No Tax Withholding data found for the current posting date."))

def get_tax_withholding_details(tax_withholding_category, posting_date, company, pan_no=None, is_adhar_compliance=None):
	tax_withholding = frappe.get_doc("Tax Withholding Category", tax_withholding_category)

	tax_rate_detail = get_tax_withholding_rates(tax_withholding, posting_date)

	for account_detail in tax_withholding.accounts:
		if company == account_detail.company:
			rate = 0.0
			if not pan_no:
				rate = tax_rate_detail.get("custom_pan_rate")
			elif not is_adhar_compliance:
				rate = tax_rate_detail.get("custom_no_aadhaar_rate")

			if not rate:
				rate = tax_rate_detail.tax_withholding_rate

			return frappe._dict(
				{
					"tax_withholding_category": tax_withholding_category,
					"account_head": account_detail.account,
					"rate": rate,
					"from_date": tax_rate_detail.from_date,
					"to_date": tax_rate_detail.to_date,
					"threshold": tax_rate_detail.single_threshold,
					"cumulative_threshold": tax_rate_detail.cumulative_threshold,
					"description": tax_withholding.category_name
					if tax_withholding.category_name
					else tax_withholding_category,
					"consider_party_ledger_amount": tax_withholding.consider_party_ledger_amount,
					"tax_on_excess_amount": tax_withholding.tax_on_excess_amount,
					"round_off_tax_amount": tax_withholding.round_off_tax_amount,
				}
			)


def get_party_details(inv):
	party_type, party = "", ""

	if inv.doctype == "Sales Invoice":
		party_type = "Customer"
		party = inv.customer
	else:
		party_type = "Supplier"
		party = inv.supplier

	if not party:
		frappe.throw(_("Please select {0} first").format(party_type))

	return party_type, party

def get_tax_amount(party_type, parties, inv, inv_row, tax_details, posting_date, pan_no=None):
	vouchers, voucher_wise_amount = get_invoice_vouchers(
		parties, tax_details, inv.company, party_type=party_type, inv_row=inv_row
	)

	payment_entry_vouchers = get_payment_entry_vouchers(
		parties, tax_details, inv.company, party_type=party_type
	)

	advance_vouchers = get_advance_vouchers(
		parties,
		company=inv.company,
		from_date=tax_details.from_date,
		to_date=tax_details.to_date,
		party_type=party_type,
	)

	taxable_vouchers = vouchers + advance_vouchers + payment_entry_vouchers
	tax_deducted_on_advances = 0

	if inv.doctype == "Purchase Invoice":
		tax_deducted_on_advances = get_taxes_deducted_on_advances_allocated(inv, tax_details)

	tax_deducted = 0
	if taxable_vouchers:
		tax_deducted = get_deducted_tax(taxable_vouchers, tax_details)

	# If advance is outside the current tax withholding period (usually a fiscal year), `get_deducted_tax` won't fetch it.
	# updating `tax_deducted` with correct advance tax value (from current and previous previous withholding periods), will allow the
	# rest of the below logic to function properly
	# ---FY 2023-------------||---------------------FY 2024-----------------------||--
	# ---Advance-------------||---------Inv_1--------Inv_2------------------------||--
	if tax_deducted_on_advances:
		tax_deducted += get_advance_tax_across_fiscal_year(tax_deducted_on_advances, tax_details)

	tax_amount = 0

	if party_type == "Supplier":
		ldc = get_lower_deduction_certificate(inv.company, tax_details, pan_no)
		if tax_deducted:
			net_total = inv_row.base_net_amount
			if ldc:
				limit_consumed = get_limit_consumed(ldc, parties)
				if is_valid_certificate(ldc, posting_date, limit_consumed):
					tax_amount = get_lower_deduction_amount(
						net_total, limit_consumed, ldc.certificate_limit, ldc.rate, tax_details
					)
				else:
					tax_amount = net_total * tax_details.rate / 100
			else:
				tax_amount = net_total * tax_details.rate / 100

			# once tds is deducted, not need to add vouchers in the invoice
			voucher_wise_amount = {}
		else:
			tax_amount = get_tds_amount(ldc, parties, inv, tax_details, vouchers, inv_row)

	elif party_type == "Customer":
		if tax_deducted:
			# if already TCS is charged, then amount will be calculated based on 'Previous Row Total'
			tax_amount = 0
		else:
			#  if no TCS has been charged in FY,
			# then chargeable value is "prev invoices + advances" value which cross the threshold
			tax_amount = get_tcs_amount(parties, inv, tax_details, vouchers, advance_vouchers)

	if cint(tax_details.round_off_tax_amount):
		tax_amount = normal_round(tax_amount)

	return tax_amount, tax_deducted, tax_deducted_on_advances, voucher_wise_amount


def get_invoice_vouchers(parties, tax_details, company, party_type="Supplier", inv_row=None):
	doctype = "Purchase Invoice" if party_type == "Supplier" else "Sales Invoice"
	child_doctype = "Purchase Invoice Item" if party_type == "Supplier" else "Sales Invoice Item"

	fields = [
		f"`tab{child_doctype}`.`base_net_amount` AS `base_net_total`",
		f"`tab{doctype}`.`name`",
	]

	voucher_wise_amount = {}
	vouchers = []

	filters = [
		[doctype, "company", "=", company],
		[doctype, "is_opening", "=", "No"],
		[doctype, "docstatus", "=", 1],
		[doctype, "posting_date", "between", (tax_details.from_date, tax_details.to_date)],
		[child_doctype, "apply_tds", "=", 1],
		[child_doctype, "custom_tax_withholding_category", "=", tax_details.get("tax_withholding_category")],
	]

	if inv_row and inv_row.item_code:
		filters.append(
			[child_doctype, "item_code", "=", inv_row.item_code]
		)

	# if doctype != "Sales Invoice":
	# 	filters.update(
	# 		{"apply_tds": 1, "tax_withholding_category": tax_details.get("tax_withholding_category")}
	# 	)

	invoices_details = frappe.get_all(
		doctype, filters=filters, fields=fields
	)

	for d in invoices_details:
		vouchers.append(d.name)
		voucher_wise_amount.update({d.name: {"amount": d.base_net_total, "voucher_type": doctype}})

	journal_entries_details = frappe.db.sql(
		"""
		SELECT j.name, ja.credit - ja.debit AS amount
			FROM `tabJournal Entry` j, `tabJournal Entry Account` ja
		WHERE
			j.name = ja.parent
			AND j.docstatus = 1
			AND j.is_opening = 'No'
			AND j.posting_date between %s and %s
			AND ja.party in %s
			AND j.apply_tds = 1
			AND j.tax_withholding_category = %s
			AND j.company = %s
	""",
		(
			tax_details.from_date,
			tax_details.to_date,
			tuple(parties),
			tax_details.get("tax_withholding_category"),
			company,
		),
		as_dict=1,
	)

	if journal_entries_details:
		for d in journal_entries_details:
			vouchers.append(d.name)
			voucher_wise_amount.update({d.name: {"amount": d.amount, "voucher_type": "Journal Entry"}})

	return vouchers, voucher_wise_amount


def get_payment_entry_vouchers(parties, tax_details, company, party_type="Supplier"):
	payment_entry_filters = {
		"party_type": party_type,
		"party": ("in", parties),
		"docstatus": 1,
		"apply_tax_withholding_amount": 1,
		"posting_date": ["between", (tax_details.from_date, tax_details.to_date)],
		"tax_withholding_category": tax_details.get("tax_withholding_category"),
		"company": company,
	}

	return frappe.db.get_all("Payment Entry", filters=payment_entry_filters, pluck="name")


def get_advance_vouchers(parties, company=None, from_date=None, to_date=None, party_type="Supplier"):
	"""
	Use Payment Ledger to fetch unallocated Advance Payments
	"""

	ple = qb.DocType("Payment Ledger Entry")

	conditions = []

	conditions.append(ple.amount.lt(0))
	conditions.append(ple.delinked == 0)
	conditions.append(ple.party_type == party_type)
	conditions.append(ple.party.isin(parties))
	conditions.append(ple.voucher_no == ple.against_voucher_no)

	if company:
		conditions.append(ple.company == company)

	if from_date and to_date:
		conditions.append(ple.posting_date[from_date:to_date])

	advances = qb.from_(ple).select(ple.voucher_no).distinct().where(Criterion.all(conditions)).run(as_list=1)
	if advances:
		advances = [x[0] for x in advances]

	return advances


def get_taxes_deducted_on_advances_allocated(inv, tax_details):
	tax_info = []

	if inv.get("advances"):
		advances = [d.reference_name for d in inv.get("advances")]

		if advances:
			pe = frappe.qb.DocType("Payment Entry").as_("pe")
			at = frappe.qb.DocType("Advance Taxes and Charges").as_("at")
			pe_items = frappe.qb.DocType("Payment Entry Item")

			query = (
				frappe.qb.from_(pe)
				.inner_join(pe_items)
				.on(pe.name == pe_items.parent)
				.inner_join(at)
				.on(pe.name == at.parent)
				.select(pe.name, pe.posting_date, at.parent, at.name, at.tax_amount, at.allocated_amount)
				.where(at.parent.isin(advances))
				.where(at.account_head == tax_details.account_head)
				.where(pe_items.custom_tax_withholding_category == tax_details.get("tax_withholding_category"))
			)

			tax_info = query.run(as_dict=1)

	return tax_info


def get_deducted_tax(taxable_vouchers, tax_details):
	# check if TDS / TCS account is already charged on taxable vouchers
	filters = {
		"is_cancelled": 0,
		"credit": [">", 0],
		"posting_date": ["between", (tax_details.from_date, tax_details.to_date)],
		"account": tax_details.account_head,
		"voucher_no": ["in", taxable_vouchers],
	}
	field = "credit"

	entries = frappe.db.get_all("GL Entry", filters, pluck=field)
	return sum(entries)


def get_advance_tax_across_fiscal_year(tax_deducted_on_advances, tax_details):
	"""
	Only applies for Taxes deducted on Advance Payments
	"""
	advance_tax_from_across_fiscal_year = sum(
		[adv.tax_amount for adv in tax_deducted_on_advances if adv.posting_date < tax_details.from_date]
	)
	return advance_tax_from_across_fiscal_year


def get_tds_amount(ldc, parties, inv, tax_details, vouchers, inv_row):
	tds_amount = 0
	invoice_filters = {"name": ("in", vouchers), "docstatus": 1, "apply_tds": 1}

	## for TDS to be deducted on advances
	payment_entry_filters = {
		"party_type": "Supplier",
		"party": ("in", parties),
		"docstatus": 1,
		"apply_tax_withholding_amount": 1,
		"unallocated_amount": (">", 0),
		"posting_date": ["between", (tax_details.from_date, tax_details.to_date)],
		"tax_withholding_category": tax_details.get("tax_withholding_category"),
		"company": inv.company,
	}

	field = "sum(tax_withholding_net_total)"

	if cint(tax_details.consider_party_ledger_amount):
		invoice_filters.pop("apply_tds", None)
		field = "sum(grand_total)"

		payment_entry_filters.pop("apply_tax_withholding_amount", None)
		payment_entry_filters.pop("tax_withholding_category", None)

	supp_credit_amt = frappe.db.get_value("Purchase Invoice", invoice_filters, field) or 0.0

	supp_jv_credit_amt = (
		frappe.db.get_value(
			"Journal Entry Account",
			{
				"parent": ("in", vouchers),
				"docstatus": 1,
				"party": ("in", parties),
				"reference_type": ("!=", "Purchase Invoice"),
			},
			"sum(credit_in_account_currency - debit_in_account_currency)",
		)
		or 0.0
	)

	# Get Amount via payment entry
	payment_entry_amounts = frappe.db.get_all(
		"Payment Entry",
		filters=payment_entry_filters,
		fields=["sum(unallocated_amount) as amount", "payment_type"],
		group_by="payment_type",
	)

	supp_credit_amt += supp_jv_credit_amt

	if inv.doctype != "Payment Entry":
		for row in inv.items:
			if row.name != inv_row.name:
				continue

			supp_credit_amt += row.base_net_amount

	for type in payment_entry_amounts:
		if type.payment_type == "Pay":
			supp_credit_amt += type.amount
		else:
			supp_credit_amt -= type.amount

	threshold = tax_details.get("threshold", 0)
	cumulative_threshold = tax_details.get("cumulative_threshold", 0)

	if inv.doctype != "Payment Entry":
		tax_withholding_net_total = inv.base_tax_withholding_net_total
	else:
		tax_withholding_net_total = inv_row.amount or inv.tax_withholding_net_total

	if inv.doctype == "Payment Entry" and inv.party_type == "Supplier":
		inv.supplier = inv.party

	if (threshold and tax_withholding_net_total >= threshold) or (
		cumulative_threshold and supp_credit_amt >= cumulative_threshold
	) or (inv.supplier and is_overlook_allowed(inv.supplier)):
		net_total = (
			frappe.db.get_value("Purchase Invoice", invoice_filters, "sum(tax_withholding_net_total)") or 0.0
		)

		supp_credit_amt += net_total

		if (cumulative_threshold and supp_credit_amt >= cumulative_threshold) and cint(
			tax_details.tax_on_excess_amount
		):
			# Get net total again as TDS is calculated on net total
			# Grand is used to just check for threshold breach
			net_total = (
				frappe.db.get_value("Purchase Invoice", invoice_filters, "sum(tax_withholding_net_total)")
				or 0.0
			)
			net_total += flt(inv_row.base_net_amount)
			supp_credit_amt = net_total - cumulative_threshold

		if not supp_credit_amt and inv.doctype == "Payment Entry":
			supp_credit_amt = tax_withholding_net_total

		if ldc and is_valid_certificate(ldc, inv.get("posting_date") or inv.get("transaction_date"), 0):
			tds_amount = get_lower_deduction_amount(
				supp_credit_amt, 0, ldc.certificate_limit, ldc.rate, tax_details
			)
		else:
			tds_amount = supp_credit_amt * tax_details.rate / 100 if supp_credit_amt > 0 else 0

	return tds_amount

def is_overlook_allowed(supplier):
	return frappe.db.get_value("Supplier Withholding Tax", {"parent": supplier}, "threshold_limit_overlook")

def get_tcs_amount(parties, inv, tax_details, vouchers, adv_vouchers):
	tcs_amount = 0
	ple = qb.DocType("Payment Ledger Entry")

	# sum of debit entries made from sales invoices
	invoiced_amt = (
		frappe.db.get_value(
			"GL Entry",
			{
				"is_cancelled": 0,
				"party_type": "Customer",
				"party": ["in", parties],
				"company": inv.company,
				"voucher_no": ["in", vouchers],
			},
			"sum(debit)",
		)
		or 0.0
	)

	# sum of credit entries made from PE / JV with unset 'against voucher'

	conditions = []
	conditions.append(ple.amount.lt(0))
	conditions.append(ple.delinked == 0)
	conditions.append(ple.party_type == "Customer")
	conditions.append(ple.party.isin(parties))
	conditions.append(ple.voucher_no == ple.against_voucher_no)
	conditions.append(ple.company == inv.company)

	qb.from_(ple).select(Abs(Sum(ple.amount))).where(Criterion.all(conditions)).run(as_list=1)

	advance_amt = (
		qb.from_(ple).select(Abs(Sum(ple.amount))).where(Criterion.all(conditions)).run()[0][0] or 0.0
	)

	# sum of credit entries made from sales invoice
	credit_note_amt = sum(
		frappe.db.get_all(
			"GL Entry",
			{
				"is_cancelled": 0,
				"credit": [">", 0],
				"party_type": "Customer",
				"party": ["in", parties],
				"posting_date": ["between", (tax_details.from_date, tax_details.to_date)],
				"company": inv.company,
				"voucher_type": "Sales Invoice",
			},
			pluck="credit",
		)
	)

	cumulative_threshold = tax_details.get("cumulative_threshold", 0)

	current_invoice_total = get_invoice_total_without_tcs(inv, tax_details)
	total_invoiced_amt = current_invoice_total + invoiced_amt + advance_amt - credit_note_amt

	if cumulative_threshold and total_invoiced_amt >= cumulative_threshold:
		chargeable_amt = total_invoiced_amt - cumulative_threshold
		tcs_amount = chargeable_amt * tax_details.rate / 100 if chargeable_amt > 0 else 0

	return tcs_amount


def get_invoice_total_without_tcs(inv, tax_details):
	tcs_tax_row = [d for d in inv.taxes if d.account_head == tax_details.account_head]
	tcs_tax_row_amount = tcs_tax_row[0].base_tax_amount if tcs_tax_row else 0

	return inv.grand_total - tcs_tax_row_amount


def get_limit_consumed(ldc, parties):
	limit_consumed = frappe.db.get_value(
		"Purchase Invoice",
		{
			"supplier": ("in", parties),
			"apply_tds": 1,
			"docstatus": 1,
			"tax_withholding_category": ldc.tax_withholding_category,
			"posting_date": ("between", (ldc.valid_from, ldc.valid_upto)),
			"company": ldc.company,
		},
		"sum(tax_withholding_net_total)",
	)

	return limit_consumed


def get_lower_deduction_amount(current_amount, limit_consumed, certificate_limit, rate, tax_details):
	if certificate_limit - flt(limit_consumed) - flt(current_amount) >= 0:
		return current_amount * rate / 100
	else:
		ltds_amount = certificate_limit - flt(limit_consumed)
		tds_amount = current_amount - ltds_amount

		return ltds_amount * rate / 100 + tds_amount * tax_details.rate / 100


def is_valid_certificate(ldc, posting_date, limit_consumed):
	available_amount = flt(ldc.certificate_limit) - flt(limit_consumed)
	if (getdate(ldc.valid_from) <= getdate(posting_date) <= getdate(ldc.valid_upto)) and available_amount > 0:
		return True

	return False


def normal_round(number):
	"""
	Rounds a number to the nearest integer.
	:param number: The number to round.
	"""
	decimal_part = number - int(number)

	if decimal_part >= 0.5:
		decimal_part = 1
	else:
		decimal_part = 0

	number = int(number) + decimal_part

	return number


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_taxwithholdig_category(doctype, txt, searchfield, start, page_len, filters):
	query_filters = {"parent": filters.get("supplier")}
	if txt:
		query_filters.update({"tax_withholding_category": ("like", "%{}%".format(txt))})

	return frappe.get_all(
		"Supplier Withholding Tax",
		fields=["tax_withholding_category"],
		filters=query_filters,
		as_list=True,
	)
