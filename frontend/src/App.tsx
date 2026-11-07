import { Center, Loader } from "@mantine/core";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthError, useMe } from "./api";
import { AppShellLayout } from "./components/AppShellLayout";
import Login from "./pages/Login";
import Privacy from "./pages/Privacy";
import HuntList from "./pages/HuntList";
import HuntBoard from "./pages/HuntBoard";
import GuildSettings from "./pages/GuildSettings";
import TeamServers from "./pages/TeamServers";
import ServerSetup from "./pages/ServerSetup";
import { ViewAsProvider } from "./context/ViewAsContext";

/** Pages that render for anyone, signed in or not (checked before the auth gate). */
export function appRouteAvailableWithoutAuth(pathname: string): boolean {
  return pathname === "/privacy" || pathname === "/privacy/";
}

export default function App() {
  const me = useMe();
  const { pathname } = useLocation();

  if (appRouteAvailableWithoutAuth(pathname)) {
    return <Privacy />;
  }

  if (me.isLoading) {
    return (
      <Center h="100vh">
        <Loader />
      </Center>
    );
  }

  if (me.error instanceof AuthError || !me.data) {
    return <Login />;
  }

  return (
    <ViewAsProvider>
      <AppShellLayout user={me.data}>
        <Routes>
          <Route path="/" element={<HuntList />} />
          <Route path="/hunts/:uid" element={<HuntBoard />} />
          <Route path="/guilds/:guildId/settings" element={<GuildSettings />} />
          <Route path="/guilds/:guildId/teams" element={<TeamServers />} />
          <Route path="/guilds/:guildId/setup" element={<ServerSetup />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShellLayout>
    </ViewAsProvider>
  );
}
