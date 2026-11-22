import { StubPage } from '../components/StubPage';

export function FaultLibrary() {
  return (
    <StubPage
      title="Fault library"
      lead="The authored catalogue: what each fault does, which taxonomy it belongs to, and which components fail most."
      sections={[
        {
          title: 'catalogue',
          body: 'Eight entries with mechanism, description, typical symptoms and blast-radius shape, one per domain and fault type.',
        },
        {
          title: 'taxonomy',
          body: 'Transient, cascading, resource exhaustion, network partition and quota limit, with the rationale for each classification.',
        },
        {
          title: 'rankings',
          body: 'Components ranked by classified scenarios, with the counts considered and excluded shown alongside.',
        },
        {
          title: 'provenance',
          body: 'The authored flag on each entry makes it clear this prose was written here, not measured from a cluster.',
        },
      ]}
      note="Faultlib serves all of this, and the Incidents form already reads the catalogue to populate fault types. This page is a stub in this build, so the list is not duplicated."
    />
  );
}
