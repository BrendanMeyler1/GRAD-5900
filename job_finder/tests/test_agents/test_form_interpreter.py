"""Tests for Form Interpreter agent."""

from agents.form_interpreter import interpret_form


def _sample_form_html() -> str:
    return """
    <form id="application_form">
      <label for="first_name">First Name</label>
      <input id="first_name" name="first_name" type="text" />

      <label for="last_name">Last Name</label>
      <input id="last_name" name="last_name" type="text" />

      <label for="email">Email</label>
      <input id="email" name="email" type="email" />

      <label for="phone">Phone</label>
      <input id="phone" name="phone" type="text" />

      <label for="resume">Resume</label>
      <input id="resume" name="resume" type="file" />

      <label for="salary_expectation">Expected Salary (USD)</label>
      <input id="salary_expectation" name="salary_expectation" type="text" />

      <label for="q1">Why do you want to work at Acme Test Corp?</label>
      <textarea id="q1" name="q1"></textarea>
    </form>
    """


def test_interpret_form_builds_fill_plan(sample_listing, sample_persona):
    fill_plan = interpret_form(
        listing=sample_listing,
        form_html=_sample_form_html(),
        persona=sample_persona,
    )

    assert fill_plan["listing_id"] == sample_listing["listing_id"]
    assert fill_plan["ats_type"] == "greenhouse"
    assert "fill_plan_id" in fill_plan
    assert isinstance(fill_plan["fields"], list)
    assert isinstance(fill_plan["escalations"], list)

    first_name = next((f for f in fill_plan["fields"] if f["field_id"] == "first_name"), None)
    assert first_name is not None
    assert first_name["selector"] == "#first_name"
    assert first_name["selector_strategy"] == "exact_css"
    assert first_name["value"] == "{{FIRST_NAME}}"

    question_field = next((f for f in fill_plan["fields"] if f.get("requires_question_responder")), None)
    assert question_field is not None
    assert question_field["value"].startswith("QUESTION_RESPONDER:")
    assert question_field["pii_level"] == "NONE"




def test_interpret_form_uses_lever_template(sample_listing, sample_persona):
    listing = dict(sample_listing)
    listing["ats_type"] = "lever"
    listing["apply_url"] = "https://jobs.lever.co/acme/123/apply"

    lever_html = """
    <form id="application-form">
      <label for="name">Full Name</label>
      <input id="name" name="name" type="text" />
      <label for="email">Email</label>
      <input id="email" name="email" type="email" />
      <label for="resume">Resume</label>
      <input id="resume" name="resume" type="file" />
    </form>
    """

    fill_plan = interpret_form(
        listing=listing,
        form_html=lever_html,
        persona=sample_persona,
    )

    assert fill_plan["ats_type"] == "lever"
    name_field = next((f for f in fill_plan["fields"] if f["field_id"] == "name"), None)
    assert name_field is not None
    assert name_field["value"] == "{{FULL_NAME}}"
    resume_field = next((f for f in fill_plan["fields"] if f["field_id"] == "resume_upload"), None)
    assert resume_field is not None


def test_interpret_form_assumes_template_selectors_when_dom_missing(sample_listing, sample_persona):
    fill_plan = interpret_form(
        listing=sample_listing,
        form_html="",
        persona=sample_persona,
    )

    first_name = next((f for f in fill_plan["fields"] if f["field_id"] == "first_name"), None)
    assert first_name is not None
    assert first_name["selector"] == "#first_name"
    assert first_name["selector_strategy"] == "template_assumed"

    selector_failures = [
        e for e in fill_plan["escalations"] if e.get("reason") == "All selector strategies failed"
    ]
    assert selector_failures == []
