frappe.ui.form.on("Purchase Invoice", {
	setup(frm) {
		frm.trigger("setup_queries");
	},

	setup_queries(frm) {
		frm.set_query("custom_tax_withholding_category", "items", function() {
			return {
				query: "tds_compliance.custom_methods.custom_purchase_invoice.get_taxwithholdig_category",
				filters: {
					"supplier": frm.doc.supplier,
				},
			};
		});
	}
});