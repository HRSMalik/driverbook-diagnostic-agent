import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL || '';

export const fetchTenantVehicles = (tenantId) =>
  axios.get(`${API_BASE}/tenants/${tenantId}/vehicles`).then(r => r.data);

export const reanalyzeVehicle = (vehicleId) =>
  axios.post(`${API_BASE}/vehicles/${vehicleId}/reanalyze`).then(r => r.data);

export const fetchUnknownFaults = () =>
  axios.get(`${API_BASE}/unknown-faults`).then(r => r.data);

export const fetchKnowledgeBase = () =>
  axios.get(`${API_BASE}/knowledge-base`).then(r => r.data);

export const fetchTenants = () =>
  axios.get(`${API_BASE}/tenants`).then(r => r.data);

export const triggerFullScan = (params = {}) =>
  axios.post(`${API_BASE}/scan`, params).then(r => r.data);
