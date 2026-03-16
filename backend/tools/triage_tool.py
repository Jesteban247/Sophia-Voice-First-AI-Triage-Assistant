# Unified triage tool for identity, clinical collection, and report generation.

import asyncio
import json
import logging
import os
import re
import boto3
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from strands import Agent
from strands.models import BedrockModel
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Image as RLImage, Table, TableStyle
)
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from io import BytesIO
from .base import BaseTool

logger = logging.getLogger(__name__)

_IMG_FMTS = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}


def _img_format(uri: str) -> str:
    ext = uri.rsplit(".", 1)[-1].split("?")[0].lower()
    return _IMG_FMTS.get(ext, "jpeg")


def _calc_age(dob_str: str) -> Optional[str]:
    # Accepts 'Month DD YYYY' or ISO 'YYYY-MM-DD' and returns age string or None.
    formats = ["%B %d %Y", "%b %d %Y", "%Y-%m-%d", "%m-%d-%Y"]
    for fmt in formats:
        try:
            dob = datetime.strptime(dob_str.strip(), fmt)
            today = datetime.now()
            age = today.year - dob.year - (
                (today.month, today.day) < (dob.month, dob.day)
            )
            return str(age)
        except ValueError:
            continue
    return None


# ── Structured Output Models ────────────────────────────────────────────────

class IdentityOut(BaseModel):
    # Structured output for patient identity collection.
    message: str = Field(
        description="Next question or confirmation message to speak to the patient"
    )
    name: Optional[str] = Field(
        None,
        description="Patient's full name as it appears on ID document"
    )
    id_number: Optional[str] = Field(
        None,
        description="ID number - digits only, no spaces or special characters"
    )
    dob: Optional[str] = Field(
        None,
        description="Date of birth in format 'Month DD YYYY' (e.g., 'November 3 2004')"
    )
    sex: Optional[str] = Field(
        None,
        description="Sex/Gender: 'Male', 'Female', or 'Other'"
    )
    done: bool = Field(
        False,
        description="Set to True only when all 4 fields (name, id_number, dob, sex) are collected"
    )


class ClinicalOut(BaseModel):
    # Structured output for clinical information collection.
    message: str = Field(
        description="Next question or response to speak to the patient"
    )
    chief_complaint: Optional[str] = Field(
        None,
        description="Chief complaint in professional third-person clinical English (e.g. 'Patient presents with headache'). Never use first person."
    )
    location: Optional[str] = Field(
        None,
        description="Anatomical location in clinical English (e.g. 'Left temporal region')."
    )
    duration: Optional[str] = Field(
        None,
        description="Duration in clinical third-person English (e.g. 'Onset approximately 3 days ago')."
    )
    severity: Optional[str] = Field(
        None,
        description="Severity rating from 1-10 as a string (e.g., '7')"
    )
    medical_history: Optional[str] = Field(
        None,
        description="Medical history in clinical third-person English (e.g. 'No known allergies, medications, or chronic conditions'). Never use first person."
    )
    visual_evidence: Optional[str] = Field(
        None,
        description="Objective clinical description of visual evidence in third-person English (e.g. 'Erythematous rash with cluster of papules observed on left forearm'). Never use first person."
    )
    evidence_ask: bool = Field(
        False,
        description="Set to True while waiting for patient to send symptom photo"
    )
    priority: Optional[str] = Field(
        None,
        description="Priority level: 'P1' (emergency), 'P2' (urgent), or 'P3' (routine)"
    )
    done: bool = Field(
        False,
        description="Set to True only when all required fields collected and at least one follow-up asked"
    )


class DoctorAdvisoryOut(BaseModel):
    # Structured output for the doctor/nurse advisory report section.
    immediate_actions: str = Field(
        description=(
            "What the nurse/doctor should do IMMEDIATELY upon receiving this patient. "
            "Specific to this case. Exactly 4 bullet points using • character."
        )
    )
    red_flags: str = Field(
        description=(
            "Warning signs to monitor closely for THIS patient. "
            "Flag any abnormal vitals explicitly. Exactly 4 bullet points using • character."
        )
    )
    differential_diagnosis: str = Field(
        description=(
            "Most likely diagnoses for this presentation, ordered by probability. "
            "Exactly 4 bullet points using • character."
        )
    )
    recommended_workup: str = Field(
        description=(
            "Specific tests, exams, and imaging recommended for this case. "
            "Exactly 4 bullet points using • character."
        )
    )
    clinical_pearls: str = Field(
        description=(
            "Key clinical considerations, pitfalls to avoid, or important context "
            "specific to this presentation. Exactly 4 bullet points using • character."
        )
    )
    monitoring: str = Field(
        description=(
            "What to monitor during the visit and follow-up instructions. "
            "Exactly 4 bullet points using • character."
        )
    )
    nursing_notes: str = Field(
        description=(
            "Specific instructions for nursing staff: positioning, comfort measures, "
            "medications to have ready, patient communication tips. "
            "Exactly 4 bullet points using • character."
        )
    )


# ── Main Tool ───────────────────────────────────────────────────────────────

class TriageTool(BaseTool):
    # Unified medical triage tool for patient information collection.

    def __init__(self):
        self.model_id = os.getenv("MODEL_ID", "global.amazon.nova-2-lite-v1:0")
        # Use Claude Sonnet for the advisory LLM — better clinical reasoning
        self.advisory_model_id = os.getenv(
            "ADVISORY_MODEL_ID", "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
        )
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.bucket = self._resolve_bucket()
        self._id_agent  = None  # Identity agent (lazy initialization)
        self._cl_agent  = None  # Clinical agent (lazy initialization)
        self._cl_agent_name_key = None  # Tracks which patient name the clinical agent was built for
        self._adv_agent = None  # Doctor advisory agent (lazy initialization)
        self._s3 = None         # S3 client (lazy initialization)

    def _resolve_bucket(self) -> str:
        bucket = os.getenv("S3_BUCKET") or os.getenv("S3_BUCKET_NAME")
        if bucket:
            return bucket
        try:
            sts = boto3.client("sts", region_name=self.region)
            account_id = sts.get_caller_identity().get("Account", "")
            project = os.getenv("PROJECT_NAME", "sonic")
            return f"{project}-images-{account_id}" if account_id else "test-nova-images"
        except Exception:
            return "test-nova-images"

    @staticmethod
    def _load_prompt(filename: str) -> str:
        # Load prompt from the prompts directory.
        prompt_path = os.path.join(os.path.dirname(__file__), "prompts", filename)
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read().strip()

    # ── BaseTool interface ───────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "triage"

    @property
    def description(self) -> str:
        return (
            "Main triage tool. Call after EVERY patient message. "
            "Handles identity collection, clinical intake, and report generation automatically."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Exact words the patient just said"
                }
            },
            "required": []
        }

    # ── Stage routing ────────────────────────────────────────────────────────

    async def execute(self, tool_use_content: dict) -> dict:
        session_id    = tool_use_content.get("session_id", "unknown")
        session_state = tool_use_content.get("session_state", {})
        stage         = session_state.get("stage", "identity")

        if stage == "identity":
            return await self._identity(tool_use_content, session_id, session_state)
        elif stage == "clinical":
            return await self._clinical(tool_use_content, session_id, session_state)
        elif stage == "vitals":
            return await self._vitals(tool_use_content, session_id, session_state)
        else:
            return {"result": json.dumps({
                "status": "complete",
                "message": "All done — the doctor will see you shortly.",
                "session_state": session_state
            })}

    # ── Stage 1: Identity Collection ────────────────────────────────────────────

    def _get_id_agent(self) -> Agent:
        # Initialize identity collection agent with external prompt.
        if not self._id_agent:
            system_prompt = self._load_prompt("identity_agent.txt")
            self._id_agent = Agent(
                model=BedrockModel(
                    model_id=self.model_id,
                    region_name=self.region,
                    streaming=False,
                    temperature=0.1,   # Low temp → deterministic structured extraction
                    max_tokens=1024,
                ),
                system_prompt=system_prompt,
                callback_handler=lambda **kw: None
            )
        return self._id_agent

    async def _identity(self, content: dict, sid: str, state: dict) -> dict:
        # Handle identity collection with voice input and optional ID photo analysis.
        user_input = content.get("input", "").strip()
        media_link = content.get("mediaLink", "")
        current = state.setdefault("identity", {})

        print(f"CONVERSATION: [{sid}] 🟢 [triage:identity] input='{user_input}'")

        if media_link:
            # ID photo analysis
            print(f"CONVERSATION: [{sid}] 🟢 [triage:identity] S3 image → {media_link}")
            photo_instructions = self._load_prompt("id_photo_analysis.txt")
            agent_input = [
                {"text": photo_instructions},
                {"image": {
                    "format": _img_format(media_link),
                    "source": {"location": {"type": "s3", "uri": media_link}}
                }}
            ]
        else:
            # Voice input
            agent_input = user_input or "Continue"

        try:
            result = await asyncio.to_thread(
                lambda: self._get_id_agent()(agent_input, structured_output_model=IdentityOut)
            )
            out: IdentityOut = result.structured_output
            print(f"CONVERSATION: [{sid}] 🟢 [triage:identity] LLM → {out.model_dump()}")

            # Update state with collected information
            if out.name:
                current["name"] = out.name
            if out.id_number:
                current["id_number"] = out.id_number
            if out.dob:
                current["dob"] = out.dob
                # Calculate age from date of birth
                age = _calc_age(out.dob)
                if age:
                    current["age"] = age
            if out.sex:
                current["sex"] = out.sex

            state["identity"] = current
            
            # Move to clinical stage when identity is complete
            if out.done:
                state["stage"] = "clinical"

            return {"result": json.dumps({
                "status": "complete" if out.done else "incomplete",
                "message": out.message,
                "request_image": False,
                "session_state": state
            }, ensure_ascii=False)}

        except Exception as e:
            logger.error(f"Identity collection error: {e}", exc_info=True)
            return {"result": json.dumps({
                "status": "incomplete",
                "message": "Sorry, could you repeat that?",
                "request_image": False,
                "session_state": state
            })}

    # ── Advisory Agent ───────────────────────────────────────────────────────────

    def _get_adv_agent(self) -> Agent:
        # Initialize doctor advisory agent with external prompt (lazy, cached).
        if not self._adv_agent:
            system_prompt = self._load_prompt("doctor_advisory.txt")
            self._adv_agent = Agent(
                model=BedrockModel(
                    model_id=self.advisory_model_id,
                    region_name=self.region,
                    streaming=False,
                    temperature=0.3,   # Moderate temp → creative but grounded clinical reasoning
                    max_tokens=2048,   # Advisory has 7 sections, needs room
                ),
                system_prompt=system_prompt,
                callback_handler=lambda **kw: None,
            )
        return self._adv_agent

    async def _generate_advisory(self, identity: dict, clinical: dict, vitals: dict) -> Optional[DoctorAdvisoryOut]:
        # Call the doctor advisory LLM to produce structured clinical guidance.
        try:
            age      = identity.get("age",  "Unknown")
            sex      = identity.get("sex",  "Unknown")
            name     = identity.get("name", "Unknown")
            cc       = clinical.get("chief_complaint", "Not specified")
            location = clinical.get("location",        "Not specified")
            duration = clinical.get("duration",        "Not specified")
            severity = clinical.get("severity",        "Not assessed")
            ve       = clinical.get("visual_evidence", "No visual evidence provided")
            hx       = clinical.get("medical_history", "No significant history reported")
            priority = clinical.get("priority",        "P3")

            hr_val  = vitals.get("hr")
            rr_val  = vitals.get("rr")
            hr_conf = vitals.get("hr_conf")
            rr_conf = vitals.get("rr_conf")

            vitals_lines = []
            if hr_val:
                conf_str = f" (confidence {hr_conf}%)" if hr_conf else ""
                vitals_lines.append(f"Heart Rate: {hr_val} bpm{conf_str}")
            if rr_val:
                conf_str = f" (confidence {rr_conf}%)" if rr_conf else ""
                vitals_lines.append(f"Respiratory Rate: {rr_val} rpm{conf_str}")
            vitals_str = "\n".join(vitals_lines) if vitals_lines else "Not available"

            case_summary = (
                f"PATIENT CASE SUMMARY\n"
                f"====================\n"
                f"Name: {name}\n"
                f"Age: {age} years old\n"
                f"Sex: {sex}\n"
                f"Priority Assigned: {priority}\n"
                f"\nCHIEF COMPLAINT\n"
                f"{cc}\n"
                f"\nLOCATION\n"
                f"{location}\n"
                f"\nDURATION\n"
                f"{duration}\n"
                f"\nSEVERITY\n"
                f"{severity}/10\n"
                f"\nVISUAL EVIDENCE\n"
                f"{ve}\n"
                f"\nMEDICAL HISTORY\n"
                f"{hx}\n"
                f"\nVITAL SIGNS\n"
                f"{vitals_str}\n"
                f"\nPlease provide all 7 structured advisory sections for this patient."
            )

            logger.info(f"[Advisory] Calling advisory LLM for case: {cc[:60]}")
            result = await asyncio.to_thread(
                lambda: self._get_adv_agent()(
                    case_summary,
                    structured_output_model=DoctorAdvisoryOut,
                )
            )
            adv: DoctorAdvisoryOut = result.structured_output
            logger.info("[Advisory] Advisory LLM returned structured output successfully")
            return adv

        except Exception as e:
            logger.error(f"[Advisory] Doctor advisory LLM failed: {e}", exc_info=True)
            return None

    # ── Stage 2: Clinical Information Collection ────────────────────────────────

    def _get_cl_agent(self, patient_name: str = "") -> Agent:
        # Initialize clinical information collection agent with external prompt.
        cache_key = patient_name or "__anonymous__"
        if not self._cl_agent or getattr(self, "_cl_agent_name_key", None) != cache_key:
            # Load base prompt and inject patient name context
            base_prompt = self._load_prompt("clinical_agent.txt")
            name_context = f"The patient's name is {patient_name}." if patient_name else ""
            system_prompt = base_prompt.replace("{patient_name_context}", name_context)

            self._cl_agent = Agent(
                model=BedrockModel(
                    model_id=self.model_id,
                    region_name=self.region,
                    streaming=False,
                    temperature=0.2,   # Low temp → consistent structured clinical output
                    max_tokens=1024,
                ),
                system_prompt=system_prompt,
                callback_handler=lambda **kw: None
            )
            self._cl_agent_name_key = cache_key  # Track which name this agent was built for
        return self._cl_agent

    async def _clinical(self, content: dict, sid: str, state: dict) -> dict:
        # Handle clinical information collection with voice input and photo analysis.
        user_input = content.get("input", "").strip()
        media_link = content.get("mediaLink", "")
        current = state.setdefault("clinical", {})
        patient_name = state.get("identity", {}).get("name", "")

        print(f"CONVERSATION: [{sid}] 🟢 [triage:clinical] input='{user_input}'")

        if media_link:
            # Clinical photo analysis
            print(f"CONVERSATION: [{sid}] 🟢 [triage:clinical] S3 image → {media_link}")
            
            # Store in both locations for compatibility
            state.setdefault("clinical_images", [])
            if media_link not in state["clinical_images"]:
                state["clinical_images"].append(media_link)
            
            # Also store in main images array for frontend display
            state.setdefault("images", [])
            if media_link not in state["images"]:
                state["images"].append(media_link)
            
            # Get context for photo analysis
            symptom = current.get("chief_complaint", "symptom")
            location = current.get("location", "affected area")
            
            # Load photo analysis instructions
            photo_instructions = self._load_prompt("clinical_photo_analysis.txt")
            photo_instructions = photo_instructions.replace("{symptom}", symptom)
            photo_instructions = photo_instructions.replace("{location}", location)
            
            agent_input = [
                {"text": photo_instructions},
                {"image": {
                    "format": _img_format(media_link),
                    "source": {"location": {"type": "s3", "uri": media_link}}
                }}
            ]
        else:
            # Voice input
            agent_input = user_input or "Continue"

        try:
            result = await asyncio.to_thread(
                lambda: self._get_cl_agent(patient_name)(agent_input, structured_output_model=ClinicalOut)
            )
            out: ClinicalOut = result.structured_output
            print(f"CONVERSATION: [{sid}] 🟢 [triage:clinical] LLM → {out.model_dump()}")

            # Update state with collected information
            if out.chief_complaint:
                current["chief_complaint"] = out.chief_complaint
            if out.location:
                current["location"] = out.location
            if out.duration:
                current["duration"] = out.duration
            if out.severity:
                current["severity"] = out.severity
            if out.medical_history:
                current["medical_history"] = out.medical_history
            if out.visual_evidence:
                current["visual_evidence"] = out.visual_evidence
            if out.priority:
                current["priority"] = out.priority

            state["clinical"] = current

            # When clinical collection is complete, trigger vitals scan before generating report
            if out.done:
                state["stage"] = "vitals"  # Wait for camera vitals scan

            return {"result": json.dumps({
                "status":         "vitals_needed" if out.done else "incomplete",
                "message":        out.message,
                "request_image":  out.evidence_ask,
                "request_vitals": out.done,      # Signal client to start vitals scan
                "priority":       out.priority,
                "report_url":     None,
                "session_state":  state
            }, ensure_ascii=False)}

        except Exception as e:
            logger.error(f"Clinical collection error: {e}", exc_info=True)
            return {"result": json.dumps({
                "status": "incomplete",
                "message": "Could you give me a bit more detail?",
                "request_image": False,
                "priority": None,
                "session_state": state
            })}

    # ── Stage 3: Vitals Scan ──────────────────────────────────────────────

    async def _vitals(self, content: dict, sid: str, state: dict) -> dict:
        # Handle vitals stage: receive scan data from client and generate PDF.
        vitals_data = content.get("vitals_data", {})
        print(f"CONVERSATION: [{sid}] 💗 [triage:vitals] data={vitals_data}")

        if vitals_data:
            state["vitals"] = vitals_data
            state["stage"]  = "done"
            report_url = await self._generate_report(sid, state)
            if report_url:
                state["report_url"] = report_url
                print(f"CONVERSATION: [{sid}] 📄 [Report] {report_url}")
            return {"result": json.dumps({
                "status":     "complete",
                "message":    "All done. Your information has been sent to the nurse. Please take a seat — they will call you in shortly.",
                "report_url": report_url,
                "session_state": state
            }, ensure_ascii=False)}
        else:
            # Waiting for vitals data (client not ready yet)
            return {"result": json.dumps({
                "status":         "waiting",
                "message":        "Please stay in front of the camera a moment longer.",
                "request_vitals": True,
                "session_state":  state
            }, ensure_ascii=False)}

    # ── PDF Report Generation ───────────────────────────────────────────────────

    def _s3_client(self):
        # Lazy initialization of S3 client.
        if not self._s3:
            self._s3 = boto3.client("s3", region_name=self.region)
        return self._s3

    async def _generate_report(self, sid: str, state: dict) -> Optional[str]:
        # Generate professional PDF medical triage report and return a presigned S3 URL.
        try:
            identity = state.get("identity", {})
            clinical = state.get("clinical", {})
            images   = state.get("clinical_images", [])
            priority  = clinical.get("priority", "P3")

            timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
            patient_name_safe = re.sub(r"[^A-Za-z0-9_]", "_", identity.get("name", "patient"))
            now_str     = datetime.now().strftime("%B %d, %Y — %H:%M")

            # ── Build PDF ────────────────────────────────────────────────
            buf = BytesIO()
            doc = SimpleDocTemplate(
                buf, pagesize=letter,
                rightMargin=54, leftMargin=54, topMargin=54, bottomMargin=36
            )

            styles = getSampleStyleSheet()
            TEAL   = colors.HexColor("#00897B")
            LTEAL  = colors.HexColor("#E0F2F1")
            DARK   = colors.HexColor("#212121")
            GREY   = colors.HexColor("#757575")

            title_style = ParagraphStyle(
                "T", parent=styles["Normal"],
                fontSize=22, textColor=TEAL,
                fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=4
            )
            sub_style = ParagraphStyle(
                "S", parent=styles["Normal"],
                fontSize=9, textColor=GREY, alignment=TA_CENTER, spaceAfter=20
            )
            section_style = ParagraphStyle(
                "H", parent=styles["Normal"],
                fontSize=11, textColor=TEAL,
                fontName="Helvetica-Bold", spaceBefore=16, spaceAfter=6
            )
            body_style = ParagraphStyle(
                "B", parent=styles["Normal"],
                fontSize=10, textColor=DARK, leading=14
            )

            P_COLORS = {
                "P1": colors.HexColor("#B71C1C"),
                "P2": colors.HexColor("#E65100"),
                "P3": colors.HexColor("#2E7D32"),
            }
            P_BG = {
                "P1": colors.HexColor("#FFEBEE"),
                "P2": colors.HexColor("#FFF3E0"),
                "P3": colors.HexColor("#E8F5E9"),
            }
            P_LABELS = {
                "P1": "EMERGENCY — Immediate attention required",
                "P2": "URGENT — Care needed within 2 hours",
                "P3": "ROUTINE — Stable, can wait",
            }

            story = []

            # Title
            story.append(Paragraph("MEDICAL TRIAGE REPORT", title_style))
            story.append(Paragraph(f"Generated: {now_str}", sub_style))
            story.append(Spacer(1, 0.1 * inch))

            # Priority banner
            p_color = P_COLORS.get(priority, GREY)
            p_bg    = P_BG.get(priority, colors.white)
            p_label = P_LABELS.get(priority, priority)
            p_table = Table(
                [[Paragraph(f"<b>Priority {priority} — {p_label}</b>",
                            ParagraphStyle("PL", parent=styles["Normal"],
                                           fontSize=12, textColor=p_color,
                                           fontName="Helvetica-Bold", alignment=TA_CENTER))]],
                colWidths=[6.5 * inch]
            )
            p_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), p_bg),
                ("BOX",        (0, 0), (-1, -1), 1.5, p_color),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING",  (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]))
            story.append(p_table)
            story.append(Spacer(1, 0.15 * inch))

            def P(text, style=None):
                # Wrap text in a Paragraph so ReportLab wraps long strings inside cells.
                return Paragraph(str(text) if text else "—", style or body_style)

            def info_table(rows, bg):
                # rows: [[label_str, value_str], ...]
                hdr_style = ParagraphStyle(
                    "TH", parent=styles["Normal"],
                    fontSize=10, fontName="Helvetica-Bold",
                    textColor=DARK, leading=13
                )
                wrapped = [[P(r[0], hdr_style), P(r[1])] for r in rows]
                t = Table(wrapped, colWidths=[1.8 * inch, 4.7 * inch])
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (0, -1), bg),
                    ("FONTSIZE",   (0, 0), (-1, -1), 10),
                    ("TEXTCOLOR",  (0, 0), (-1, -1), DARK),
                    ("ALIGN",      (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN",     (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                    ("GRID",       (0, 0), (-1, -1), 0.5, colors.HexColor("#BDBDBD")),
                ]))
                return t

            # Patient Identity
            story.append(Paragraph("PATIENT IDENTITY", section_style))
            story.append(info_table([
                ["Name",          identity.get("name",       "—")],
                ["ID Number",     identity.get("id_number",  "—")],
                ["Date of Birth", identity.get("dob",        "—")],
                ["Age",           identity.get("age",        "—")],
                ["Sex",           identity.get("sex",        "—")],
            ], LTEAL))

            # Clinical Information
            story.append(Paragraph("CLINICAL INFORMATION", section_style))
            sev = clinical.get("severity")
            sev_display = f"{sev} / 10" if sev else "Not assessed"
            
            # Format clinical data professionally with proper capitalization
            def format_text(text, default="Not specified"):
                # Format text with proper capitalization and grammar.
                if not text or text.lower() in ["", "none", "—"]:
                    return default
                # Capitalize first letter and ensure proper sentence structure
                text = text.strip()
                if text and text[0].islower():
                    text = text[0].upper() + text[1:]
                # Ensure it ends with proper punctuation for descriptions
                if len(text) > 20 and not text[-1] in ['.', '!', '?']:
                    text = text + '.'
                return text
            
            chief_complaint = format_text(clinical.get("chief_complaint"), "Not specified")
            location        = format_text(clinical.get("location"),        "Not specified")
            duration        = format_text(clinical.get("duration"),        "Not specified")
            visual_evidence = format_text(clinical.get("visual_evidence"), "No visual evidence provided")
            medical_history = format_text(clinical.get("medical_history"), "No significant medical history reported")
            
            story.append(info_table([
                ["Chief Complaint", chief_complaint],
                ["Location",       location],
                ["Duration",       duration],
                ["Severity",       sev_display],
                ["Visual Evidence",visual_evidence],
                ["Medical History",medical_history],
            ], colors.HexColor("#F9FBE7")))
            story.append(Spacer(1, 0.1 * inch))

            # PAGE BREAK — Patient info on page 1, clinical advice follows on page 2+
            story.append(PageBreak())

            # Clinical images
            if images:
                story.append(Paragraph("VISUAL EVIDENCE", section_style))
                story.append(Paragraph(
                    "Patient-submitted clinical photographs for diagnostic reference:",
                    ParagraphStyle("intro", parent=styles["Normal"],
                                   fontSize=9, textColor=GREY, spaceAfter=10)
                ))
                s3 = self._s3_client()
                for idx, img_uri in enumerate(images, 1):
                    try:
                        key      = img_uri.replace(f"s3://{self.bucket}/", "")
                        obj      = s3.get_object(Bucket=self.bucket, Key=key)
                        img_data = obj["Body"].read()
                        img_buf  = BytesIO(img_data)
                        img      = RLImage(img_buf, width=4.5 * inch, height=3.375 * inch,
                                          kind="proportional")
                        story.append(img)
                        story.append(Paragraph(
                            f"<i>Figure {idx}: Clinical photograph submitted by patient</i>",
                            ParagraphStyle("cap", parent=styles["Normal"],
                                           fontSize=8, textColor=GREY, 
                                           alignment=TA_CENTER, spaceAfter=12)
                        ))
                    except Exception as ie:
                        logger.error(f"Image embed error: {ie}")
                        story.append(Paragraph(
                            f"[Figure {idx}: Image could not be embedded]",
                            ParagraphStyle("err", parent=styles["Normal"],
                                           fontSize=9, textColor=GREY, 
                                           alignment=TA_CENTER, spaceAfter=8)
                        ))
            else:
                story.append(Paragraph("VISUAL EVIDENCE", section_style))
                story.append(Paragraph(
                    "No clinical images provided.",
                    ParagraphStyle("intro", parent=styles["Normal"],
                                   fontSize=9, textColor=GREY, spaceAfter=10)
                ))

            # Vital Signs (available because _vitals() stores data before calling us)
            vitals  = state.get("vitals", {})
            hr_val  = vitals.get("hr")  if vitals else None
            rr_val  = vitals.get("rr")  if vitals else None
            hr_conf = vitals.get("hr_conf") if vitals else None
            rr_conf = vitals.get("rr_conf") if vitals else None

            if vitals and (hr_val or rr_val):
                story.append(Paragraph("VITAL SIGNS", section_style))
                story.append(info_table([
                    ["Heart Rate",  f"{hr_val} bpm" + (f"   (Conf: {hr_conf}%)" if hr_conf else "") if hr_val else "—"],
                    ["Respiration", f"{rr_val} rpm" + (f"   (Conf: {rr_conf}%)" if rr_conf else "") if rr_val else "—"],
                ], colors.HexColor("#E3F2FD")))

                chart_imgs = []
                s3 = self._s3_client()
                for chart_key in ("hr_chart", "rr_chart"):
                    chart_uri = vitals.get(chart_key)
                    if chart_uri and chart_uri.startswith("s3://"):
                        try:
                            key = chart_uri.replace(f"s3://{self.bucket}/", "")
                            obj = s3.get_object(Bucket=self.bucket, Key=key)
                            chart_img = RLImage(BytesIO(obj["Body"].read()),
                                                width=3.0 * inch, height=1.5 * inch,
                                                kind="proportional")
                            chart_imgs.append(chart_img)
                        except Exception as ce:
                            logger.error(f"Vitals chart embed error: {ce}")
                if chart_imgs:
                    empties = [""] * (2 - len(chart_imgs))
                    ct = Table([chart_imgs + empties], colWidths=[3.3 * inch, 3.3 * inch])
                    ct.setStyle(TableStyle([
                        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ]))
                    story.append(Spacer(1, 6))
                    story.append(ct)

            # ── Doctor Advisory (dedicated LLM step) ────────────────────────────────
            logger.info(f"[{sid}] Calling doctor advisory LLM ...")
            advisory = await self._generate_advisory(
                identity=identity,
                clinical=clinical,
                vitals=vitals or {},
            )

            # Helper: render a single advisory block (title + bullet content)
            adv_section_style = ParagraphStyle(
                "AdvH", parent=styles["Normal"],
                fontSize=10, textColor=TEAL,
                fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=4,
            )
            adv_body_style = ParagraphStyle(
                "AdvB", parent=styles["Normal"],
                fontSize=9, textColor=DARK, leading=14, leftIndent=6,
            )

            def normalize_bullets(text: str, count: int = 4):
                # Normalize advisory content to exactly `count` bullet items.
                if not text:
                    return ["—"] * count
                parts = re.split(r"[•\n]+", text)
                cleaned = []
                for part in parts:
                    item = part.strip(" \t-–—•")
                    if item:
                        cleaned.append(item)
                if not cleaned:
                    cleaned = ["—"]
                if len(cleaned) < count:
                    cleaned += ["—"] * (count - len(cleaned))
                return cleaned[:count]

            def adv_block(title: str, content: str, bg: str):
                # Render a titled advisory section as a padded colour box.
                bg_color = colors.HexColor(bg)
                border   = colors.HexColor("#BDBDBD")
                # Build a consistent bullet list for layout stability.
                bullets = normalize_bullets(content, 4)
                html_content = "<br/>".join([f"• {item}" for item in bullets])
                title_para   = Paragraph(title, adv_section_style)
                content_para = Paragraph(html_content, adv_body_style)
                inner = Table(
                    [[title_para], [content_para]],
                    colWidths=[6.5 * inch],
                )
                inner.setStyle(TableStyle([
                    ("BACKGROUND",    (0, 0), (-1, -1), bg_color),
                    ("BOX",           (0, 0), (-1, -1), 0.75, border),
                    ("TOPPADDING",    (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("LEFTPADDING",   (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
                    ("LINEBELOW",     (0, 0), (-1, 0),  0.5, TEAL),
                ]))
                return inner

            story.append(Spacer(1, 0.15 * inch))
            story.append(Paragraph("CLINICAL ADVISORY FOR MEDICAL STAFF", section_style))
            story.append(Paragraph(
                "The following AI-generated advisory is intended to assist nursing and medical staff. "
                "All clinical decisions must be made by qualified professionals.",
                ParagraphStyle("adv_intro", parent=styles["Normal"],
                               fontSize=8, textColor=GREY, spaceAfter=6),
            ))

            if advisory:
                story.append(adv_block(
                    "●  IMMEDIATE ACTIONS",
                    advisory.immediate_actions,
                    "#FFF3E0",   # amber-light
                ))
                story.append(Spacer(1, 4))
                story.append(adv_block(
                    "⚠  RED FLAGS & WARNING SIGNS",
                    advisory.red_flags,
                    "#FFEBEE",   # red-light
                ))
                story.append(Spacer(1, 4))
                story.append(adv_block(
                    "⊙  DIFFERENTIAL DIAGNOSIS",
                    advisory.differential_diagnosis,
                    "#E8EAF6",   # indigo-light
                ))
                story.append(Spacer(1, 4))
                story.append(adv_block(
                    "☑  RECOMMENDED WORKUP",
                    advisory.recommended_workup,
                    "#E8F5E9",   # green-light
                ))
                story.append(Spacer(1, 4))
                story.append(adv_block(
                    "★  CLINICAL PEARLS",
                    advisory.clinical_pearls,
                    "#F3E5F5",   # purple-light
                ))
                story.append(Spacer(1, 4))
                story.append(adv_block(
                    "⌛  MONITORING & FOLLOW-UP",
                    advisory.monitoring,
                    "#E0F2F1",   # teal-light
                ))
                story.append(Spacer(1, 4))
                story.append(adv_block(
                    "♥  NURSING NOTES",
                    advisory.nursing_notes,
                    "#F5F5F5",   # grey-light
                ))
            else:
                # Minimal fallback if advisory LLM failed
                p_fallback = {
                    "P1": "• Escort to emergency bay immediately\n• Physician assessment within 15 min\n• Prepare emergency equipment",
                    "P2": "• Seen within 2 hours\n• Monitor for symptom progression\n• Alert physician of urgent case",
                    "P3": "• Standard queue processing\n• Regular monitoring if wait > 1 hour",
                }
                story.append(adv_block(
                    "●  CARE GUIDANCE",
                    p_fallback.get(priority, p_fallback["P3"]),
                    "#FFF9C4",
                ))
            
            # ── Triage Summary ──────────────────────────────────────────────────
            story.append(Spacer(1, 0.15 * inch))
            story.append(Paragraph("TRIAGE SUMMARY", section_style))
            
            # Generate a concise summary for quick reference
            age_val = identity.get("age", "Unknown")
            sex_val = identity.get("sex", "Unknown")
            
            # Extract clean complaint without "Patient presents with" prefix
            chief_complaint_raw = clinical.get('chief_complaint', 'unspecified complaint')
            if chief_complaint_raw.lower().startswith('patient presents with '):
                chief_complaint_clean = chief_complaint_raw[22:]  # Remove "Patient presents with "
            elif chief_complaint_raw.lower().startswith('patient reports '):
                chief_complaint_clean = chief_complaint_raw[16:]  # Remove "Patient reports "
            else:
                chief_complaint_clean = chief_complaint_raw
            
            # Extract clean location
            location_raw = clinical.get('location', 'unspecified location')
            location_clean = location_raw.replace('Location: ', '').replace('location: ', '')
            
            # Extract clean duration
            duration_raw = clinical.get('duration', 'unspecified duration')
            if duration_raw.lower().startswith('onset approximately '):
                duration_clean = duration_raw[20:]  # Remove "Onset approximately "
            elif duration_raw.lower().startswith('symptoms present for '):
                duration_clean = duration_raw[21:]  # Remove "Symptoms present for "
            else:
                duration_clean = duration_raw
            
            summary_text = (
                f"<b>{age_val}-year-old {sex_val.lower()}</b> presenting with "
                f"<b>{chief_complaint_clean.lower()}</b> "
                f"in the <b>{location_clean.lower()}</b> "
                f"for <b>{duration_clean.lower()}</b>. "
            )
            
            if sev:
                summary_text += f"Pain/discomfort rated <b>{sev}/10</b>. "
            
            if hr_val and rr_val:
                summary_text += f"Vitals: HR <b>{hr_val} bpm</b>, RR <b>{rr_val} rpm</b>. "
            
            summary_text += f"Triaged as <b>{priority}</b>."
            
            summary_para = Paragraph(
                summary_text,
                ParagraphStyle("summary", parent=styles["Normal"],
                              fontSize=10, textColor=DARK, leading=16,
                              leftIndent=10, rightIndent=10,
                              spaceBefore=8, spaceAfter=8)
            )
            
            summary_table = Table([[summary_para]], colWidths=[6.5 * inch])
            summary_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F5F5")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#BDBDBD")),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ]))
            story.append(summary_table)
            
            # ── Footer ──────────────────────────────────────────────────────────
            story.append(Spacer(1, 0.2 * inch))
            footer_text = (
                "<i>This triage report was generated by an AI-assisted system. "
                "All clinical decisions should be made by qualified medical professionals "
                "based on direct patient assessment and clinical judgment. "
                "This report is for informational purposes only.</i>"
            )
            story.append(Paragraph(
                footer_text,
                ParagraphStyle("footer", parent=styles["Normal"],
                              fontSize=7, textColor=GREY,
                              alignment=TA_CENTER, leading=10)
            ))

            # Build PDF once (all sections complete)
            doc.build(story)

            # Upload to S3 → presigned URL (7 days)
            pdf_key = f"reports/{timestamp}_{patient_name_safe}.pdf"
            s3 = self._s3_client()
            s3.put_object(
                Bucket=self.bucket,
                Key=pdf_key,
                Body=buf.getvalue(),
                ContentType="application/pdf"
            )
            url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": pdf_key},
                ExpiresIn=604800  # 7 days
            )
            return url

        except Exception as e:
            logger.error(f"Report generation error: {e}", exc_info=True)
            return None
