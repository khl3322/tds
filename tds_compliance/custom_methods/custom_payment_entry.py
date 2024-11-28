import frappe
import erpnext

from frappe import _, qb
from frappe.query_builder import Criterion
from frappe.query_builder.functions import Abs, Sum
from frappe.utils import cint, flt, getdate
from erpnext.accounts.doctype.tax_withholding_category.tax_withholding_category import get_lower_deduction_certificate, get_cost_center

from erpnext.controllers.accounts_controller import validate_account_head


from erpnext.accounts.doctype.payment_entry.payment_entry import PaymentEntry


class CustomPaymentEntry(PaymentEntry):
	def calculate_taxes(self):
		self.total_taxes_and_charges = 0.0
		self.base_total_taxes_and_charges = 0.0

		actual_tax_dict = dict(
			[
				[tax.idx, flt(tax.tax_amount, tax.precision("tax_amount"))]
				for tax in self.get("taxes")
				if tax.charge_type == "Actual"
			]
		)

		self.paid_amount_after_tax = self.paid_amount if self.party_type == "Supplier" else self.received_amount
		for i, tax in enumerate(self.get("taxes")):
			current_tax_amount = self.get_current_tax_amount(tax)

			if tax.charge_type == "Actual":
				actual_tax_dict[tax.idx] -= current_tax_amount
				if i == len(self.get("taxes")) - 1:
					current_tax_amount += actual_tax_dict[tax.idx]

			tax.tax_amount = current_tax_amount
			tax.base_tax_amount = current_tax_amount

			if tax.add_deduct_tax == "Deduct":
				current_tax_amount *= -1.0
			else:
				current_tax_amount *= 1.0

			if i == 0:
				tax.total = flt(self.paid_amount_after_tax + current_tax_amount, self.precision("total", tax))
			else:
				tax.total = flt(
					self.get("taxes")[i - 1].total + current_tax_amount, self.precision("total", tax)
				)

			tax.base_total = tax.total

			if self.payment_type == "Pay":
				if tax.currency != self.paid_to_account_currency:
					self.total_taxes_and_charges += flt(current_tax_amount / self.target_exchange_rate)
				else:
					self.total_taxes_and_charges += current_tax_amount
			elif self.payment_type == "Receive":
				if tax.currency != self.paid_from_account_currency:
					self.total_taxes_and_charges += flt(current_tax_amount / self.source_exchange_rate)
				else:
					self.total_taxes_and_charges += current_tax_amount

			self.base_total_taxes_and_charges += tax.base_tax_amount

		if self.get("taxes"):
			self.paid_amount_after_tax = self.get("taxes")[-1].base_total


	def get_order_wise_tax_withholding_net_total(self):
		if self.party_type == "Supplier":
			doctype = "Purchase Order"
		else:
			doctype = "Sales Order"

		docnames = [d.reference_name for d in self.references if d.reference_doctype == doctype]

		return frappe._dict(
			frappe.db.get_all(
				doctype,
				filters={"name": ["in", docnames]},
				fields=["name", "base_tax_withholding_net_total"],
				as_list=True,
			)
		)

	def calculate_tax_withholding_net_total(self):
		net_total = 0
		order_details = self.get_order_wise_tax_withholding_net_total()

		for d in self.references:
			tax_withholding_net_total = order_details.get(d.reference_name)
			if not tax_withholding_net_total:
				continue

			net_taxable_outstanding = max(
				0, d.outstanding_amount - (d.total_amount - tax_withholding_net_total)
			)

			net_total += min(net_taxable_outstanding, d.allocated_amount)

		net_total += self.unallocated_amount

		return net_total

	def set_base_net_amount(self):
		if not self.advance_payment_percentage:
			return

		if self.advance_payment_percentage > 100:
			frappe.throw("Advance Payment Percentage cannot be more than 100")

		self.tax_withholding_net_total = 0.0
		for row in self.items:
			row.rate = row.price_list_rate * self.advance_payment_percentage / 100
			row.amount = (row.rate * row.qty) - (row.tds_amount * self.advance_payment_percentage / 100)
			row.base_net_amount = row.amount
			row.net_amount = row.amount

			self.tax_withholding_net_total += row.amount

		self.total = self.tax_withholding_net_total
		self.base_total = self.tax_withholding_net_total
		self.paid_amount = self.tax_withholding_net_total

		for row in self.references:
			row.allocated_amount = self.paid_amount

		self.total_allocated_amount = self.paid_amount

	def set_tax_withholding(self):
		self.set_base_net_amount()
		if not self.items:
			return

		if self.party_type != "Supplier":
			return

		net_total = self.calculate_tax_withholding_net_total()

		# Adding args as purchase invoice to get TDS amount
		args = frappe._dict(
			{
				"company": self.company,
				"doctype": "Payment Entry",
				"supplier": self.party,
				"posting_date": self.posting_date,
				"net_total": net_total,
			}
		)

		tax_holding_details = frappe._dict({})
		for inv_row in self.items:
			if not inv_row.custom_tax_withholding_category:
				continue

			tax_withholding_details = get_party_tax_withholding_details(args, inv_row, inv_row.custom_tax_withholding_category)

			if not tax_withholding_details:
				continue

			tax_withholding_details.update(
				{"cost_center": self.cost_center or erpnext.get_default_cost_center(self.company)}
			)

			accounts = []
			for d in self.taxes:
				if d.account_head == tax_withholding_details.get("account_head"):
					d.update(tax_withholding_details)

				accounts.append(d.account_head)

			if not accounts or tax_withholding_details.get("account_head") not in accounts:
				self.append("taxes", tax_withholding_details)

			to_remove = [
				d
				for d in self.taxes
				if not d.rate and d.account_head == tax_withholding_details.get("account_head")
			]

			for d in to_remove:
				self.remove(d)

			if tax_withholding_details.get("account_head") not in tax_holding_details:
				tax_holding_details.setdefault(tax_withholding_details.get("account_head"), tax_withholding_details)
			elif tax_withholding_details.get("account_head") in tax_holding_details:
				tax_holding_details[tax_withholding_details.get("account_head")]["tax_amount"] += tax_withholding_details.get("tax_amount")

		if not tax_holding_details:
			return

		for account_head, tax_details in tax_holding_details.items():
			if not tax_details.get("tax_amount"):
				continue

			tax_details.update({
				"base_tax_amount": tax_details.get("tax_amount"),
			})
			self.append("taxes", tax_details)

		self.set_amounts_after_tax()
		self.calculate_taxes()

def on_save(doc, method = None):
	pass