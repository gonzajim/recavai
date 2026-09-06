// src/UserManagement.js
// Panel de gestión de usuarios de Firebase Auth. Renderizado solo para el
// administrador autorizado (ver App.js) — el backend vuelve a comprobar el
// email en cada petición (require_admin_or_403 en app.py), así que este
// componente no es la única barrera de seguridad, solo la de UI.
import React, { useState, useEffect, useCallback } from 'react';
import { auth } from './firebase';
import {
  Box, Button, Dialog, DialogTitle, DialogContent, DialogActions, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, Paper, TextField,
  Typography, CircularProgress, Switch, FormControlLabel, IconButton, Chip,
  Alert, Tooltip,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import AddIcon from '@mui/icons-material/Add';

// TODO: cuando exista el proyecto de producción real, seleccionar la URL
// según el hosting igual que hace bubble-prod-script.js (endpoints prod/dev).
const ORCHESTRATOR_BASE_URL = 'https://orchestrator-dev-370417116045.europe-west1.run.app';

const emptyForm = { email: '', password: '', display_name: '', email_verified: false };

function formatDate(ms) {
  if (!ms) return '—';
  return new Date(ms).toLocaleString('es-ES', { dateStyle: 'medium', timeStyle: 'short' });
}

function UserManagement() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState(emptyForm);
  const [createError, setCreateError] = useState('');
  const [saving, setSaving] = useState(false);

  const [editingUser, setEditingUser] = useState(null);
  const [editForm, setEditForm] = useState(emptyForm);
  const [editError, setEditError] = useState('');

  const [deletingUser, setDeletingUser] = useState(null);
  const [deleteError, setDeleteError] = useState('');

  const getAuthToken = async () => {
    if (!auth.currentUser) throw new Error('No autenticado.');
    return auth.currentUser.getIdToken();
  };

  const apiCall = async (path, options = {}) => {
    const token = await getAuthToken();
    const resp = await fetch(`${ORCHESTRATOR_BASE_URL}${path}`, {
      ...options,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(options.headers || {}),
      },
    });
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok || payload.ok === false) {
      throw new Error(payload?.error?.message || `Error del servidor (${resp.status})`);
    }
    return payload.data;
  };

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await apiCall('/admin/users');
      setUsers(data.users || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const handleCreate = async () => {
    setCreateError('');
    if (!createForm.email || !createForm.password) {
      setCreateError('Email y contraseña son obligatorios.');
      return;
    }
    setSaving(true);
    try {
      await apiCall('/admin/users', { method: 'POST', body: JSON.stringify(createForm) });
      setCreateOpen(false);
      setCreateForm(emptyForm);
      setNotice('Usuario creado correctamente.');
      fetchUsers();
    } catch (err) {
      setCreateError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const openEdit = (u) => {
    setEditingUser(u);
    setEditForm({
      email: u.email || '',
      password: '',
      display_name: u.display_name || '',
      email_verified: !!u.email_verified,
      disabled: !!u.disabled,
    });
    setEditError('');
  };

  const handleEditSave = async () => {
    if (!editingUser) return;
    setEditError('');
    setSaving(true);
    try {
      const body = {
        email: editForm.email,
        display_name: editForm.display_name,
        email_verified: editForm.email_verified,
        disabled: editForm.disabled,
      };
      if (editForm.password) body.password = editForm.password;
      await apiCall(`/admin/users/${editingUser.uid}`, { method: 'PATCH', body: JSON.stringify(body) });
      setEditingUser(null);
      setNotice('Usuario actualizado correctamente.');
      fetchUsers();
    } catch (err) {
      setEditError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deletingUser) return;
    setDeleteError('');
    setSaving(true);
    try {
      await apiCall(`/admin/users/${deletingUser.uid}`, { method: 'DELETE' });
      setDeletingUser(null);
      setNotice('Usuario eliminado correctamente.');
      fetchUsers();
    } catch (err) {
      setDeleteError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box sx={{ padding: { xs: 1, sm: 2, md: 3 } }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h5">Gestión de usuarios</Typography>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => { setCreateForm(emptyForm); setCreateError(''); setCreateOpen(true); }}>
          Añadir usuario
        </Button>
      </Box>

      {notice && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setNotice('')}>{notice}</Alert>}
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {loading ? (
        <Box display="flex" justifyContent="center" p={8}><CircularProgress /></Box>
      ) : (
        <TableContainer component={Paper}>
          <Table sx={{ minWidth: 650 }}>
            <TableHead>
              <TableRow sx={{ '& th': { fontWeight: 'bold', backgroundColor: '#fafafa' } }}>
                <TableCell>Email</TableCell>
                <TableCell>Nombre</TableCell>
                <TableCell>Estado</TableCell>
                <TableCell>Creado</TableCell>
                <TableCell>Último acceso</TableCell>
                <TableCell align="right">Acciones</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {users.map((u) => (
                <TableRow key={u.uid} hover>
                  <TableCell>{u.email}{u.is_admin && <Chip label="admin" size="small" color="primary" sx={{ ml: 1 }} />}</TableCell>
                  <TableCell>{u.display_name || '—'}</TableCell>
                  <TableCell>
                    {u.disabled
                      ? <Chip label="Deshabilitado" size="small" color="default" />
                      : (u.email_verified
                        ? <Chip label="Verificado" size="small" color="success" />
                        : <Chip label="Sin verificar" size="small" color="warning" />)}
                  </TableCell>
                  <TableCell>{formatDate(u.created_at)}</TableCell>
                  <TableCell>{formatDate(u.last_sign_in_at)}</TableCell>
                  <TableCell align="right">
                    <Tooltip title="Editar">
                      <IconButton size="small" onClick={() => openEdit(u)}><EditIcon fontSize="small" /></IconButton>
                    </Tooltip>
                    <Tooltip title={u.is_admin ? 'No se puede eliminar al administrador' : 'Eliminar'}>
                      <span>
                        <IconButton size="small" disabled={u.is_admin} onClick={() => { setDeletingUser(u); setDeleteError(''); }}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </span>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
              {users.length === 0 && (
                <TableRow><TableCell colSpan={6} align="center">No hay usuarios registrados.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {/* Crear usuario */}
      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Añadir usuario</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
          {createError && <Alert severity="error">{createError}</Alert>}
          <TextField label="Email" type="email" value={createForm.email}
            onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })} autoFocus fullWidth />
          <TextField label="Contraseña" type="password" value={createForm.password}
            onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
            helperText="Mínimo 6 caracteres" fullWidth />
          <TextField label="Nombre (opcional)" value={createForm.display_name}
            onChange={(e) => setCreateForm({ ...createForm, display_name: e.target.value })} fullWidth />
          <FormControlLabel
            control={<Switch checked={createForm.email_verified}
              onChange={(e) => setCreateForm({ ...createForm, email_verified: e.target.checked })} />}
            label="Marcar email como verificado" />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)} disabled={saving}>Cancelar</Button>
          <Button variant="contained" onClick={handleCreate} disabled={saving}>
            {saving ? <CircularProgress size={22} /> : 'Crear'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Editar usuario */}
      <Dialog open={!!editingUser} onClose={() => setEditingUser(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Editar usuario</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
          {editError && <Alert severity="error">{editError}</Alert>}
          <TextField label="Email" type="email" value={editForm.email}
            onChange={(e) => setEditForm({ ...editForm, email: e.target.value })} fullWidth
            disabled={!!editingUser?.is_admin} helperText={editingUser?.is_admin ? 'No se puede cambiar el email del administrador' : ''} />
          <TextField label="Nueva contraseña (opcional)" type="password" value={editForm.password}
            onChange={(e) => setEditForm({ ...editForm, password: e.target.value })}
            helperText="Déjalo en blanco para no cambiarla" fullWidth />
          <TextField label="Nombre" value={editForm.display_name}
            onChange={(e) => setEditForm({ ...editForm, display_name: e.target.value })} fullWidth />
          <FormControlLabel
            control={<Switch checked={editForm.email_verified}
              onChange={(e) => setEditForm({ ...editForm, email_verified: e.target.checked })} />}
            label="Email verificado" />
          <FormControlLabel
            control={<Switch checked={editForm.disabled} disabled={!!editingUser?.is_admin}
              onChange={(e) => setEditForm({ ...editForm, disabled: e.target.checked })} />}
            label="Cuenta deshabilitada" />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditingUser(null)} disabled={saving}>Cancelar</Button>
          <Button variant="contained" onClick={handleEditSave} disabled={saving}>
            {saving ? <CircularProgress size={22} /> : 'Guardar'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Confirmar borrado */}
      <Dialog open={!!deletingUser} onClose={() => setDeletingUser(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Eliminar usuario</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
          {deleteError && <Alert severity="error">{deleteError}</Alert>}
          <Typography>
            ¿Seguro que quieres eliminar la cuenta <strong>{deletingUser?.email}</strong>? Esta acción no se puede deshacer.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeletingUser(null)} disabled={saving}>Cancelar</Button>
          <Button variant="contained" color="error" onClick={handleDelete} disabled={saving}>
            {saving ? <CircularProgress size={22} /> : 'Eliminar'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

export default UserManagement;
