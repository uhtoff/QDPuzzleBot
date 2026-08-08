import { Center, Loader } from "@mantine/core";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthError, useMe } from "./api";
import { AppShellLayout } from "./components/AppShellLayout";
import Login from "./pages/Login";
import HuntList from "./pages/HuntList";
import HuntBoard from "./pages/HuntBoard";
import GuildSettings from "./pages/GuildSettings";
import { ViewAsProvider } from "./context/ViewAsContext";

export default function App() {
  const me = useMe();

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
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShellLayout>
    </ViewAsProvider>
  );
}
