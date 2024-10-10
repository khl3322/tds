import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	custom_fields = {
		"Supplier": [
			dict(
				fieldname="custom_tds",
				label="TDS",
				fieldtype="Tab Break",
				insert_after="language",
				read_only=0,
				print_hide=0,
			),
			dict(
				fieldname="custom_pan_number",
				label="Pan Number",
				fieldtype="Data",
				insert_after="custom_tds",
				read_only=0,
				print_hide=0,
			),
			dict(
				fieldname="custom_has_withholding_tax",
				label="Has Withholding Tax",
				fieldtype="Check",
				insert_after="custom_pan_number",
				read_only=0,
				print_hide=0,
			),
			dict(
				fieldname="custom_is_aadhar_link_compliance",
				label="Is Aadhar Link Compliance",
				fieldtype="Check",
				insert_after="custom_has_withholding_tax",
				depends_on="custom_has_withholding_tax",
				read_only=0,
				print_hide=0,
			),
			dict(
				fieldname="custom_taxes",
				label="Taxes",
				fieldtype="Table",
				insert_after="custom_lower_tds_certificate_rate",
				depends_on="custom_has_withholding_tax",
				options="Supplier Withholding Tax",
				read_only=0,
				print_hide=0,
			)
		],
		"Purchase Invoice Item": [
			dict(
				fieldname="custom_section_break_ricb4",
				label="Taxwithholding Details",
				fieldtype="Section Break",
				insert_after="is_free_item",
				read_only=0,
				print_hide=0,
			),
			dict(
				fieldname="custom_tax_withholding_category",
				label="Tax Withholding Category",
				fieldtype="Link",
				insert_after="apply_tds",
				options="Tax Withholding Category",
				read_only=0,
				print_hide=0,
			),
		],
		"Tax Withholding Rate": [
			dict(
				fieldname="custom_non_compliance_of",
				label="Non Compliance of",
				fieldtype="Section Break",
				insert_after="cumulative_threshold",
				read_only=0,
				print_hide=0,
			),
			dict(
				fieldname="custom_pan_rate",
				label="No Pan Rate",
				fieldtype="Float",
				insert_after="custom_non_compliance_of",
				read_only=0,
				print_hide=0,
			),
			dict(
				fieldname="custom_no_aadhaar_rate",
				label="No Aadhaar Link Rate",
				fieldtype="Float",
				insert_after="custom_pan_rate",
				read_only=0,
				print_hide=0,
			),
		],
	}

	create_custom_fields(custom_fields)