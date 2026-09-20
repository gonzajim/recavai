// src/App.js
import React, { useState, useEffect } from 'react';
import { onAuthStateChanged, signOut } from 'firebase/auth';
import { auth } from './firebase';
import Login from './Login';
import ChatHistoryViewer from './ChatHistoryViewer';
import UserManagement from './UserManagement';

// --- NUEVAS IMPORTACIONES PARA EL TEMA ---
import { ThemeProvider } from '@mui/material/styles';
import CssBaseline from '@mui/material/CssBaseline';
import theme from './theme'; // Importamos nuestro tema personalizado
import { AppBar, Toolbar, Typography, Button, Box, Paper, Tabs, Tab } from '@mui/material';

// Único email con acceso a la pestaña de gestión de usuarios. El backend
// (require_admin_or_403 en app.py) vuelve a comprobar esto en cada petición,
// así que ocultar la pestaña aquí es solo una comodidad de UI, no la barrera real.
const ADMIN_EMAIL = 'gonzalo.jimenez.martin@gmail.com';

function App() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState('history');

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (currentUser) => {
      setUser(currentUser);
      setLoading(false);
    });
    return () => unsubscribe();
  }, []);

  if (loading) {
    return <div>Cargando...</div>;
  }

  const isAdmin = user?.email?.toLowerCase() === ADMIN_EMAIL;

  // Usamos el ThemeProvider para envolver toda la aplicación
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline /> {/* Normaliza los estilos del navegador */}
      <Box sx={{ flexGrow: 1 }}>
        {/* Cabecera superior con el estilo UCLM */}
        <AppBar position="static">
          <Toolbar>
            <Typography variant="h6" component="div" sx={{ flexGrow: 1 }}>
              Panel de Experto ReCaVa
            </Typography>
            {user && (
              <Button color="inherit" onClick={() => signOut(auth)}>Cerrar Sesión</Button>
            )}
          </Toolbar>
          {user && isAdmin && (
            <Tabs value={tab} onChange={(_, v) => setTab(v)} textColor="inherit" indicatorColor="secondary" sx={{ px: 2 }}>
              <Tab value="history" label="Historial de chat" />
              <Tab value="users" label="Gestión de usuarios" />
            </Tabs>
          )}
        </AppBar>

        {/* Contenido Principal */}
        <main>
          {user ? (
            (tab === 'users' && isAdmin) ? <UserManagement /> : <ChatHistoryViewer />
          ) : (
            <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '80vh' }}>
              <Paper elevation={3} sx={{ padding: 4 }}>
                <Login />
              </Paper>
            </Box>
          )}
        </main>
      </Box>
    </ThemeProvider>
  );
}

export default App;