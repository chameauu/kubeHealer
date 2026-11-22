import { StubPage } from '../components/StubPage';

export function ChaosLab() {
  return (
    <StubPage
      title="Chaos lab"
      lead="A workbench for the faultable simulator: its inventory, the fault rules currently active, and the lifecycle of each resource."
      sections={[
        {
          title: 'inventory',
          body: 'Volumes, subnets and instances with their current state and zone, grouped by resource type.',
        },
        {
          title: 'fault rules',
          body: 'Every rule applied to the simulator — fault type, resource filter, probability, hits and expiry.',
        },
        {
          title: 'provisioning',
          body: 'Move a resource through its states and watch a fault land on attach, detach, provision, create or delete.',
        },
        {
          title: 'scenario queue',
          body: 'Simulator scenarios the chaos service has applied, with their lifecycle and the rule each one created.',
        },
      ]}
      note="The simulator exposes this state; this build does not render it. Simulator scenarios can still be injected on Incidents, and no inventory is invented on this page."
    />
  );
}
