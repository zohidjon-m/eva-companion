import { Fragment, useMemo, useState, type ReactNode } from "react";
import { Button, EmptyState, Icon } from "../components";
import { ProfileArt } from "../sections/illustrations";
import {
  fetchProfileEvidence,
  type ProfileClaim,
  type ProfileEvidenceDetail,
  type ProfileEvidenceRef,
  type ProfileSection,
} from "./api";
import { useProfile } from "./useProfile";

/**
 * ProfileScreen — Phase 13. What Eva understands about you, made visible and
 * editable. It renders profile.md (the human-readable view of the structured
 * profile.json) and lets you correct it: your edits are saved back through the
 * lenient §7.2 sync as your own anchored corrections.
 *
 * Two modes: a calm reading view of the rendered Markdown, and a plain-text
 * editor over the same Markdown. The render is deliberately a small, dependency-
 * free subset (headings, bold, lists, paragraphs) matching exactly what the
 * backend emits — the app ships offline, so we don't pull in a Markdown library.
 *
 * When there is no profile (a fresh vault, or a deleted profile.json), the screen
 * shows the warm "still getting to know you" empty state rather than an error —
 * the same graceful degrade the chat path takes.
 */

export function ProfileScreen() {
  const p = useProfile();

  if (p.loading) {
    return <p className="profile__status">Reading your profile…</p>;
  }

  if (p.error && !p.present) {
    return <p className="profile__error">{p.error}</p>;
  }

  if (!p.present) {
    return (
      <EmptyState
        illustration={<ProfileArt />}
        eyebrow="Profile"
        title="Eva is still getting to know you"
        description="As you talk and journal, Eva builds a private picture of what you care about — your goals, your values, the people who matter. You'll always be able to read it, edit it, and delete any of it."
        action={
          <Button variant="secondary" disabled>
            No profile yet
          </Button>
        }
      />
    );
  }

  if (p.editing) {
    return <Editor p={p} />;
  }

  return (
    <div className="profile">
      <div className="profile__bar">
        <p className="profile__bar-note">
          This is yours to correct. Edits are kept as your own and Eva won't overwrite them.
        </p>
        <Button
          variant="secondary"
          size="sm"
          iconBefore={<Icon name="feather" size={15} />}
          onClick={p.startEdit}
        >
          Edit
        </Button>
      </div>

      {p.savedTick > 0 && <Saved />}
      {p.warnings.length > 0 && <Warnings warnings={p.warnings} />}

      <article className="profile__doc">
        <Markdown source={p.markdown} sections={p.sections} />
      </article>
    </div>
  );
}

/* ── Edit mode ─────────────────────────────────────────────────────────────── */

function Editor({ p }: { p: ReturnType<typeof useProfile> }) {
  return (
    <div className="profile profile--editing">
      <div className="profile__bar">
        <p className="profile__bar-note">
          Editing your profile. Change the wording of any line; Eva will treat it as your
          own correction.
        </p>
      </div>

      {p.error && <p className="profile__error">{p.error}</p>}

      <textarea
        className="profile__editor"
        value={p.draft}
        disabled={p.saving}
        onChange={(e) => p.setDraft(e.target.value)}
        aria-label="Edit your profile (Markdown)"
        spellCheck
      />

      <div className="profile__footer">
        <Button variant="ghost" size="md" onClick={p.cancelEdit} disabled={p.saving}>
          Cancel
        </Button>
        <Button variant="primary" size="md" onClick={p.save} disabled={p.saving}>
          {p.saving ? "Saving…" : "Save changes"}
        </Button>
      </div>
    </div>
  );
}

function Saved() {
  return (
    <p className="profile__saved" role="status">
      <Icon name="sparkle" size={15} /> Saved. Eva will reflect your changes from now on.
    </p>
  );
}

function Warnings({ warnings }: { warnings: string[] }) {
  return (
    <div className="profile__warnings" role="status">
      <p className="profile__warnings-head">
        <Icon name="alert" size={15} /> Some changes were left as they were:
      </p>
      <ul>
        {warnings.map((w, i) => (
          <li key={i}>{w}</li>
        ))}
      </ul>
    </div>
  );
}

/* ── A tiny Markdown renderer (the subset profile.md uses) ───────────────────
 * Headings (#, ##), unordered lists (-), paragraphs, and inline **bold** /
 * _italic_. Faithful to exactly what backend render_markdown emits — not a
 * general Markdown engine. Kept here, dependency-free, because Eva ships offline.
 */

function Markdown({ source, sections }: { source: string; sections: ProfileSection[] }) {
  const blocks: ReactNode[] = [];
  const lines = source.split("\n");
  const claims = useMemo(() => sections.flatMap((section) => section.claims), [sections]);
  const shown = new Set<string>();
  let i = 0;
  let key = 0;

  const evidenceFor = (line: string) => {
    const matches = claimsForLine(line, claims).filter((claim) => !shown.has(claim.id));
    matches.forEach((claim) => shown.add(claim.id));
    return matches;
  };

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === "") {
      i++;
      continue;
    }

    // Headings.
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      const text = h[2].trim();
      if (level === 1) blocks.push(<h2 key={key++} className="profile__h1">{inline(text)}</h2>);
      else if (level === 2) blocks.push(<h3 key={key++} className="profile__h2">{inline(text)}</h3>);
      else blocks.push(<h4 key={key++} className="profile__h3">{inline(text)}</h4>);
      i++;
      continue;
    }

    // A run of bullet lines → one list.
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*]\s+/, "").trim());
        i++;
      }
      blocks.push(
        <ul key={key++} className="profile__list">
          {items.map((it, k) => {
            const matched = evidenceFor(it);
            return (
              <li key={k}>
                <span>{inline(it)}</span>
                {matched.map((claim) => (
                  <ClaimEvidence key={claim.id} claim={claim} />
                ))}
              </li>
            );
          })}
        </ul>,
      );
      continue;
    }

    // Otherwise a paragraph (a single line; backend separates paragraphs by blanks).
    const matched = evidenceFor(line);
    blocks.push(
      <div key={key++} className="profile__p-wrap">
        <p className="profile__p">{inline(line.trim())}</p>
        {matched.map((claim) => (
          <ClaimEvidence key={claim.id} claim={claim} />
        ))}
      </div>,
    );
    i++;
  }

  return <>{blocks}</>;
}

function ClaimEvidence({ claim }: { claim: ProfileClaim }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, ProfileEvidenceDetail | "error">>({});
  const refs = claim.evidence;

  const open = async (ref: ProfileEvidenceRef) => {
    if (!ref.available) return;
    if (openId === ref.id) {
      setOpenId(null);
      return;
    }
    setOpenId(ref.id);
    if (details[ref.id]) return;
    setLoadingId(ref.id);
    try {
      const detail = await fetchProfileEvidence(ref.id);
      setDetails((prev) => ({ ...prev, [ref.id]: detail }));
    } catch {
      setDetails((prev) => ({ ...prev, [ref.id]: "error" }));
    } finally {
      setLoadingId(null);
    }
  };

  return (
    <div className="profile-evidence">
      <div className="profile-evidence__head">
        <Icon name="shield-check" size={13} />
        <span>{claimStatus(claim)}</span>
      </div>
      {refs.length > 0 ? (
        <div className="profile-evidence__refs">
          {refs.map((ref) => {
            const detail = details[ref.id];
            const isOpen = openId === ref.id;
            return (
              <div key={ref.id} className="profile-evidence__item">
                <button
                  type="button"
                  className="profile-evidence__ref"
                  onClick={() => void open(ref)}
                  disabled={!ref.available}
                  aria-expanded={isOpen}
                  title={ref.available ? "Show entry" : "Evidence entry unavailable"}
                >
                  <span className="profile-evidence__date">
                    {ref.date ?? "Missing entry"}
                  </span>
                  <span className="profile-evidence__preview">
                    {ref.available ? ref.preview : "This evidence pointer no longer resolves."}
                  </span>
                </button>
                {isOpen && (
                  <div className="profile-evidence__detail">
                    {loadingId === ref.id ? (
                      <p>Loading evidence...</p>
                    ) : detail === "error" ? (
                      <p>Could not load this entry.</p>
                    ) : detail ? (
                      <>
                        <p className="profile-evidence__meta">
                          {detail.date} / {detail.type}
                        </p>
                        <p>{detail.text}</p>
                      </>
                    ) : null}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="profile-evidence__empty">{claimStatus(claim)}</p>
      )}
    </div>
  );
}

function claimStatus(claim: ProfileClaim): string {
  if (claim.anchored || claim.source === "user") return "User corrected";
  if (claim.evidence_status === "available") return `${claim.evidence.length} evidence entries`;
  if (claim.evidence_status === "partial") return "Some evidence available";
  if (claim.evidence_status === "missing") return "Evidence missing";
  if (claim.evidence_status === "none") return "No evidence attached yet";
  return "Evidence";
}

function claimsForLine(line: string, claims: ProfileClaim[]): ProfileClaim[] {
  const normalizedLine = normalizeClaimText(line);
  if (!normalizedLine) return [];
  return claims.filter((claim) => {
    const normalizedClaim = normalizeClaimText(claim.text);
    return (
      normalizedClaim.length > 0 &&
      (normalizedLine.includes(normalizedClaim) || normalizedClaim.includes(normalizedLine))
    );
  });
}

function normalizeClaimText(text: string): string {
  return text
    .replace(/\*\*/g, "")
    .replace(/_/g, "")
    .replace(/[—–]/g, "-")
    .toLowerCase()
    .replace(/[^a-z0-9+\-\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Inline formatting: **bold** and _italic_. Splits on the markers, in order. */
function inline(text: string): ReactNode {
  // Bold first, then italic within each non-bold span.
  const parts: ReactNode[] = [];
  const boldRe = /\*\*(.+?)\*\*/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = boldRe.exec(text)) !== null) {
    if (m.index > last) parts.push(<Fragment key={key++}>{italic(text.slice(last, m.index))}</Fragment>);
    parts.push(<strong key={key++}>{italic(m[1])}</strong>);
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(<Fragment key={key++}>{italic(text.slice(last))}</Fragment>);
  return parts.length === 1 ? parts[0] : parts;
}

/** Inline italics: _text_. Returns the string unchanged when there's no marker. */
function italic(text: string): ReactNode {
  const re = /_(.+?)_/g;
  if (!re.test(text)) return text;
  re.lastIndex = 0;
  const parts: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(<Fragment key={key++}>{text.slice(last, m.index)}</Fragment>);
    parts.push(<em key={key++}>{m[1]}</em>);
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(<Fragment key={key++}>{text.slice(last)}</Fragment>);
  return parts;
}
