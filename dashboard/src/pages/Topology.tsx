import { StubPage } from '../components/StubPage';

export function Topology() {
  return (
    <StubPage
      title="Topology"
      lead="The service graph behind blast radius: nodes, edges, and what a fault would reach from a chosen target."
      sections={[
        {
          title: 'graph',
          body: 'Services grouped by namespace, with env_ref and flow_observed edges between them, as reported by the graph service.',
        },
        {
          title: 'blast radius explorer',
          body: 'Choose a namespace, fault type and label selector; read matched and affected nodes with hop distance and severity.',
        },
        {
          title: 'provenance',
          body: 'Every view carries its topology sources, so a synthetic fixture is labelled as synthetic and never read as a measured cluster.',
        },
        {
          title: 'focus',
          body: 'Select a node to see only what reaches it, which is the question an operator asks before approving a restart.',
        },
      ]}
      note="The graph service already exposes /topology and /blast-radius — the Incidents form previews blast radius with them. This build does not draw the map, and no sample topology is drawn here."
    />
  );
}
