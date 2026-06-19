def __getattr__(name: str):
    if name in {"parse_form3_xml", "parse_form4_xml", "parse_schedule13d_xml", "parse_schedule13g_xml"}:
        from . import ownership

        return getattr(ownership, name)
    if name in {"parse_form_n_cen_xml", "parse_form_n_mfp2_xml", "parse_form_n_mfp3_xml", "parse_form_npx_xml", "parse_nport_p_xml"}:
        from . import fund_and_ownership

        return getattr(fund_and_ownership, name)
    if name in __all__:
        from . import regulatory_forms

        return getattr(regulatory_forms, name)
    raise AttributeError(name)

__all__ = [
    "parse_abs_ee_comments_xml",
    "parse_abs_ee_xml",
    "parse_any_xml",
    "parse_effect_xml",
    "parse_form13f_hr_xml",
    "parse_form144_xml",
    "parse_form25_xml",
    "parse_form3_xml",
    "parse_form4_xml",
    "parse_form_c_xml",
    "parse_form_d_xml",
    "parse_form_n_cen_xml",
    "parse_form_n_mfp2_xml",
    "parse_form_n_mfp3_xml",
    "parse_form_npx_xml",
    "parse_nport_p_xml",
    "parse_schedule13d_xml",
    "parse_schedule13g_xml",
]
