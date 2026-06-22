import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Center, Code, Stack, Text, Title } from "@mantine/core";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Top-level error boundary. Without this, any uncaught render error unmounts the
 * whole React tree and the user is left staring at a blank white page. Here we
 * catch it, log it for debugging, and show a recoverable fallback.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the detail in the console so the blank-page-turned-fallback is still
    // debuggable in production.
    console.error("Unhandled render error:", error, info.componentStack);
  }

  handleReload = () => {
    // A full reload is the most reliable way to recover from a corrupted tree.
    window.location.reload();
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <Center h="100vh" p="md">
        <Stack align="center" gap="sm" maw={600}>
          <Title order={3}>Something went wrong</Title>
          <Text c="dimmed" ta="center" size="sm">
            The dashboard hit an unexpected error and couldn&apos;t render this view.
            Reloading usually fixes it.
          </Text>
          <Code block w="100%" style={{ whiteSpace: "pre-wrap" }}>
            {error.message || String(error)}
          </Code>
          <Button onClick={this.handleReload}>Reload</Button>
        </Stack>
      </Center>
    );
  }
}
