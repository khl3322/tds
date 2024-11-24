frappe.ui.form.on("Payment Entry", {
    refresh(frm) {
        if (frm.doc.purchase_taxes_and_charges_template && !frm.doc.taxes?.length) {
            frm.trigger("purchase_taxes_and_charges_template");
        }
    }
});