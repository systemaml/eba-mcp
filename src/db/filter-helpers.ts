import { SearchFilters } from './types.js';

// Canonical value stored and matched for AML/CFT topic.
const AML_CFT_CANONICAL = 'AML/CFT';

// Heuristic title terms used when the corpus topic field uses a publication-type
// facet (e.g. "EBA guidelines") instead of the subject-area label.
const AML_TITLE_PREDICATES = `
    OR lower(d.title) LIKE '%aml%'
    OR lower(d.title) LIKE '%cft%'
    OR lower(d.title) LIKE '%mltf%'
    OR lower(d.title) LIKE '%ml/tf%'
    OR lower(d.title) LIKE '%money laundering%'
    OR lower(d.title) LIKE '%terrorist financing%'
    OR lower(d.title) LIKE '%customer due diligence%'
    OR lower(d.title) LIKE '%remote customer onboarding%'
    OR lower(d.title) LIKE '%compliance officer%'
    OR lower(d.title) LIKE '%asylum seeker%'`;

/**
 * Appends a topic condition to `conditions`/`params`.
 * For topic="AML/CFT" (case-insensitive) expands to heuristic title terms so
 * documents tagged with a publication-facet topic ("EBA guidelines") are also
 * matched.  Always binds the canonical value regardless of caller's casing.
 */
export function addTopicFilter(
  conditions: string[],
  params: unknown[],
  filters: SearchFilters,
): void {
  if (!filters.topic) {
    return;
  }

  if (filters.topic.toUpperCase() !== AML_CFT_CANONICAL) {
    conditions.push('d.topic = ?');
    params.push(filters.topic);
    return;
  }

  conditions.push(`(upper(d.topic) = ?${AML_TITLE_PREDICATES})`);
  params.push(AML_CFT_CANONICAL);
}

/** Variant for listDocuments which joins without the `d.` alias prefix. */
export function addTopicFilterNoAlias(
  conditions: string[],
  params: unknown[],
  filters: SearchFilters,
): void {
  if (!filters.topic) {
    return;
  }

  const titlePreds = AML_TITLE_PREDICATES.replace(/d\.title/g, 'title');

  if (filters.topic.toUpperCase() !== AML_CFT_CANONICAL) {
    conditions.push('topic = ?');
    params.push(filters.topic);
    return;
  }

  conditions.push(`(upper(topic) = ?${titlePreds})`);
  params.push(AML_CFT_CANONICAL);
}

/**
 * Appends a section-path exclusion that removes parsed consultation-response
 * appendix chunks from search results.  Only applied when
 * `filters.exclude_consultation_responses` is true.
 */
export function addConsultationResponseExclusion(
  conditions: string[],
  filters: SearchFilters,
): void {
  if (!filters.exclude_consultation_responses) {
    return;
  }

  conditions.push(`NOT (
    lower(c.section_path) LIKE '%feedback on%consultation%'
    OR lower(c.section_path) LIKE '%summary of responses%consultation%'
    OR lower(c.section_path) LIKE '%public consultation%'
    OR lower(c.section_path) LIKE '%analysis of responses%'
  )`);
}
