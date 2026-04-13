import { useState } from "react";

export default function NewApplicationPanel({
  onRefreshQueue,
  onRefreshPersona
}) {
  const [selectedFile, setSelectedFile] = useState(null);
  const [resumeBusy, setResumeBusy] = useState(false);
  const [resumeMessage, setResumeMessage] = useState("");
  
  const [jobUrl, setJobUrl] = useState("");
  const [jobBusy, setJobBusy] = useState(false);
  const [jobMessage, setJobMessage] = useState("");

  const [seedBusy, setSeedBusy] = useState(false);

  const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";

  const handleFileChange = (event) => {
    if (event.target.files && event.target.files.length > 0) {
      setSelectedFile(event.target.files[0]);
    }
  };

  const uploadResume = async () => {
    if (!selectedFile) return;
    setResumeBusy(true);
    setResumeMessage("Processing resume with Profile Analyst LLM...");

    const formData = new FormData();
    formData.append("file", selectedFile);

    try {
      const res = await fetch(`${API_BASE}/persona/upload`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Upload failed");
      
      setResumeMessage("✅ Resume successfully analyzed and persona injected!");
      if (onRefreshPersona) onRefreshPersona();
      setSelectedFile(null);
    } catch (err) {
      setResumeMessage(`❌ Error: ${err.message}`);
    } finally {
      setResumeBusy(false);
    }
  };

  const scrapeJob = async () => {
    if (!jobUrl) return;
    setJobBusy(true);
    setJobMessage("Scraping URL and evaluating fit...");

    try {
      const res = await fetch(`${API_BASE}/jobs/add-by-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: jobUrl, use_llm: false })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Scraping failed");
      
      setJobMessage("✅ Job scraped and added to queue!");
      setJobUrl("");
      if (onRefreshQueue) onRefreshQueue();
    } catch (err) {
      setJobMessage(`❌ Error: ${err.message}`);
    } finally {
      setJobBusy(false);
    }
  };

  const seedQueue = async () => {
    if (seedBusy) return;
    setSeedBusy(true);
    setJobMessage("Seeding queue with reference ATS jobs...");

    try {
      const res = await fetch(`${API_BASE}/seed-queue`, {
        method: "POST"
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || "Seeding failed");
      
      setJobMessage("✅ Test jobs queued! They will appear below.");
      if (onRefreshQueue) onRefreshQueue();
    } catch (err) {
      setJobMessage(`❌ Error: ${err.message}`);
    } finally {
      setSeedBusy(false);
    }
  };

  return (
    <section className="card">
      <h2>Add New Application</h2>
      <p className="card-subtitle">
        Inject a real resume and job listing to test end-to-end.
      </p>

      {/* Resume Upload Module */}
      <div className="list-item" style={{ marginTop: "14px" }}>
        <p className="list-title">1. Upload Resume</p>
        <div className="list-meta">PDF or DOCX format</div>
        <div className="toolbar">
          <input 
            type="file" 
            accept=".pdf,.docx,.txt" 
            onChange={handleFileChange} 
            disabled={resumeBusy}
            style={{ fontSize: "12px", width: "100%" }}
          />
        </div>
        <div className="toolbar">
          <button 
            className="btn btn-primary" 
            onClick={uploadResume} 
            disabled={!selectedFile || resumeBusy}
          >
            {resumeBusy ? "Analyzing..." : "Upload & Parse Persona"}
          </button>
        </div>
        {resumeMessage && <div className="message">{resumeMessage}</div>}
      </div>

      {/* Job URL Scraper Module */}
      <div className="list-item" style={{ marginTop: "14px" }}>
        <p className="list-title">2. Paste Job URL</p>
        <div className="list-meta">Greenhouse or Lever links work best</div>
        <input
          className="escalation-input"
          value={jobUrl}
          placeholder="https://boards.greenhouse.io/..."
          onChange={(e) => setJobUrl(e.target.value)}
          disabled={jobBusy}
        />
        <div className="toolbar" style={{ display: "flex", gap: "8px" }}>
          <button 
            className="btn btn-primary" 
            onClick={scrapeJob} 
            disabled={!jobUrl || jobBusy || seedBusy}
            style={{ flex: 1 }}
          >
            {jobBusy ? "Scraping..." : "Scrape & Queue Job"}
          </button>

          <button 
            className="btn btn-secondary" 
            onClick={seedQueue} 
            disabled={seedBusy || jobBusy}
            title="Automatically queue 3 test ATS links to bypass finding URLs manually"
            style={{ flex: 1 }}
          >
            {seedBusy ? "Seeding..." : "Seed Dummy Jobs"}
          </button>
        </div>
        {jobMessage && <div className="message">{jobMessage}</div>}
      </div>
    </section>
  );
}
