document.addEventListener('DOMContentLoaded', () => {
  const rememberedEmail = localStorage.getItem('occAssistRememberedEmail');
  const loginForm = document.querySelector('#login-form');
  const messageBox = document.querySelector('#form-message');

  if (loginForm) {
    if (rememberedEmail && loginForm.email) {
      loginForm.email.value = rememberedEmail;
      if (loginForm.remember) {
        loginForm.remember.checked = true;
      }
    }

    loginForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const email = loginForm.email.value.trim();
      const password = loginForm.password.value;
      const remember = loginForm.remember.checked;

      if (!email || !password || password.length < 8) {
        setMessage(messageBox, 'Please provide a valid email and a password with at least 8 characters.', 'error');
        return;
      }

      setMessage(messageBox, 'Signing in securely...', 'success');

      const response = await fetch(loginForm.dataset.loginUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email, password }),
      });
      const payload = await response.json();

      if (!response.ok) {
        setMessage(messageBox, payload.message || 'Unable to sign in.', 'error');
        return;
      }

      if (remember) {
        localStorage.setItem('occAssistRememberedEmail', email);
      } else {
        localStorage.removeItem('occAssistRememberedEmail');
      }

      window.location.href = payload.redirect;
    });
  }

  const logoutButton = document.querySelector('[data-action="logout"]');
  if (logoutButton) {
    logoutButton.addEventListener('click', async () => {
      await fetch(window.OCC_ASSIST.logoutUrl, { method: 'POST' });
      window.location.href = '/';
    });
  }

  initializeUsersPage();
  initializeMap();
  initializeServiceOverview();
  initializeDrivingHours();
  initializeDailyOverview();
  initializeSettingsPage();
  initializeContactsPage();
});

function setMessage(element, message, variant = '') {
  if (!element) {
    return;
  }

  element.textContent = message;
  element.className = variant ? `message ${variant}` : 'message';
}

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function normalizeTrackingKey(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function formatVehicleDirection(value) {
  const direction = String(value || '').trim().toLowerCase();
  if (direction === 'inbound') {
    return 'Inbound';
  }
  if (direction === 'outbound') {
    return 'Outbound';
  }
  return direction ? direction.charAt(0).toUpperCase() + direction.slice(1) : 'Unknown';
}

function formatBoardNumber(vehicle) {
  return String(
    vehicle?.boardNumber
      || vehicle?.blockRef
      || vehicle?.journeyCode
      || vehicle?.vehicleJourneyRef
      || vehicle?.journeyRef
      || 'Unknown',
  ).trim() || 'Unknown';
}

function formatJourneyNumber(vehicle) {
  return String(
    vehicle?.journeyCode
      || vehicle?.vehicleJourneyRef
      || vehicle?.journeyRef
      || 'Unknown',
  ).trim() || 'Unknown';
}

function formatJourneyOriginDeparture(vehicle) {
  const rawTime = (
    vehicle?.originAimedDepartureTime
      || vehicle?.originDepartureTime
      || vehicle?.firstStopDepartureTime
      || ''
  );
  const value = String(rawTime).trim();
  if (!value) {
    return 'Not available';
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatLastStop(lastStop) {
  if (!lastStop) {
    return 'Not yet available';
  }
  return String(lastStop.name || 'Unknown stop').trim() || 'Unknown stop';
}

function formatRouteLabel(vehicle) {
  return String(vehicle?.routeLabel || vehicle?.service || 'Unknown').trim() || 'Unknown';
}



function initializeMap() {
  const mapContainer = document.querySelector('#map');
  const mapStatus = document.querySelector('#map-status');
  const routeToggle = document.querySelector('#static-routes-toggle');
  const routeSelect = document.querySelector('#static-route-select');
  const directionSelect = document.querySelector('#static-direction-select');
  const routeStatus = document.querySelector('#map-route-status');
  const stopToggle = document.querySelector('#bus-stops-toggle');
  const trackingApp = document.querySelector('#tracking-app');
  const sidebarEmpty = document.querySelector('#tracking-sidebar-empty');
  const sidebarPanel = document.querySelector('#tracking-sidebar-panel');
  const selectedService = document.querySelector('#tracking-selected-service');
  const selectedRoute = document.querySelector('#tracking-selected-route');
  const selectedFleet = document.querySelector('#tracking-selected-fleet');
  const selectedDirection = document.querySelector('#tracking-selected-direction');
  const selectedDirectionLabel = document.querySelector('#tracking-selected-direction-label');
  const selectedDestination = document.querySelector('#tracking-selected-destination');
  const selectedBoard = document.querySelector('#tracking-selected-board');
  const selectedJourney = document.querySelector('#tracking-selected-journey');
  const selectedPunctuality = document.querySelector('#tracking-selected-punctuality');
  const selectedOriginDeparture = document.querySelector('#tracking-selected-origin-departure');
  const selectedLastStop = document.querySelector('#tracking-selected-last-stop');
  const selectedUpdated = document.querySelector('#tracking-selected-updated');
  const selectedStopPanel = document.querySelector('#tracking-stop-panel');
  const selectedStopName = document.querySelector('#tracking-selected-stop-name');
  const selectedStopCode = document.querySelector('#tracking-selected-stop-code');
  const selectedStopId = document.querySelector('#tracking-selected-stop-id');
  const selectedStopArrivals = document.querySelector('#tracking-selected-stop-arrivals');
  const trackingSearchType = document.querySelector('#tracking-search-type');
  const trackingSearchQuery = document.querySelector('#tracking-search-query');
  const trackingSearchSubmit = document.querySelector('#tracking-search-submit');
  const trackingFollowToggle = document.querySelector('#tracking-follow-toggle');
  const boltonCenter = [-2.428219, 53.576864];
  const hasMapboxToken = Boolean(window.MAPBOX_TOKEN && window.MAPBOX_TOKEN !== 'YOUR_MAPBOX_ACCESS_TOKEN_HERE');
  let map = null;
  let mapAvailable = false;
  const canRenderMapOverlays = () => Boolean(map && typeof map.isStyleLoaded === 'function' && map.isStyleLoaded());
  if (!mapContainer) {
    return;
  }

  const syncMapContainerSize = () => {
    const mapShell = document.querySelector('.map-shell-tracking');
    if (!mapContainer || !mapShell) {
      return;
    }
    const shellHeight = mapShell.clientHeight > 0 ? mapShell.clientHeight : 320;
    mapContainer.style.position = 'relative';
    mapContainer.style.width = '100%';
    mapContainer.style.height = `${shellHeight}px`;
    mapContainer.style.minHeight = `${shellHeight}px`;
  };

  const resizeMap = () => {
    if (!mapAvailable || !map) {
      return;
    }
    syncMapContainerSize();
    map.resize();
  };

  const initializeOverlays = () => {
    loadTrackingStops();
    startVehicleRefresh();
    loadStaticRoutes();
    window.requestAnimationFrame(() => {
      resizeMap();
      window.requestAnimationFrame(resizeMap);
    });
    window.setTimeout(resizeMap, 250);
  };

  if (!hasMapboxToken || typeof mapboxgl === 'undefined') {
    mapContainer.innerHTML = `
      <div class="placeholder-card map-fallback-card">
        <p>Mapbox is not available right now, so the tracking page is showing the public OpenStreetMap fallback view while the live service data continues loading.</p>
        <iframe
          class="map-fallback-iframe"
          src="https://www.openstreetmap.org/export/embed.html?bbox=-2.95%2C53.3%2C-1.9%2C53.9&layer=mapnik&marker=53.576864%2C-2.428219"
          title="OpenStreetMap fallback"
          loading="lazy"
        ></iframe>
      </div>
    `;
    if (mapStatus) {
      setMessage(mapStatus, 'Showing the fallback map while live tracking data is being refreshed.', 'success');
    }
    if (trackingApp) {
      document.body.classList.add('tracking-active');
    }
    if (routeSelect) routeSelect.disabled = true;
    if (directionSelect) directionSelect.disabled = true;
    if (routeStatus) routeStatus.textContent = 'Mapbox is unavailable. Live data is still loading in the sidebar and service overview.';
    if (sidebarEmpty) sidebarEmpty.textContent = 'Select a bus marker to inspect its service details.';
    if (window.OCC_ASSIST?.trackingVehiclesUrl) {
      fetch(window.OCC_ASSIST.trackingVehiclesUrl, { cache: 'no-store' })
        .then((response) => response.json())
        .then((payload) => {
          const vehicles = Array.isArray(payload?.vehicles) ? payload.vehicles : [];
          if (vehicles.length && selectedService && selectedRoute && selectedFleet && selectedDirection && selectedDirectionLabel && selectedDestination && selectedBoard && selectedJourney && selectedPunctuality && selectedOriginDeparture && selectedLastStop && selectedUpdated) {
            const firstVehicle = vehicles[0];
            const fleetDisplay = String(firstVehicle?.fleetNumber || 'Unknown').trim() || 'Unknown';
            selectedService.textContent = fleetDisplay;
            selectedRoute.textContent = String(firstVehicle?.service || 'Unknown').trim() || 'Unknown';
            selectedFleet.textContent = fleetDisplay;
            selectedDirection.textContent = formatVehicleDirection(firstVehicle?.direction);
            selectedDirectionLabel.textContent = formatVehicleDirection(firstVehicle?.direction);
            selectedDestination.textContent = String(firstVehicle?.destination || 'Unknown').trim() || 'Unknown';
            selectedBoard.textContent = formatBoardNumber(firstVehicle);
            selectedJourney.textContent = formatJourneyNumber(firstVehicle);
            selectedPunctuality.textContent = firstVehicle?.punctuality?.label || 'Unknown';
            selectedPunctuality.className = `sidebar-pill punctuality-pill ${firstVehicle?.punctuality?.tone || 'neutral'}`;
            selectedOriginDeparture.textContent = formatJourneyOriginDeparture(firstVehicle);
            selectedLastStop.textContent = formatLastStop(firstVehicle?.lastStopPassed);
            selectedUpdated.textContent = `Updated ${formatFeedTime(firstVehicle?.recordedAt || firstVehicle?.sourceTimestamp || firstVehicle?.refreshedAt)}`;
            if (sidebarEmpty) sidebarEmpty.hidden = true;
            if (sidebarPanel) sidebarPanel.hidden = false;
          }
          if (mapStatus) {
            const updated = formatFeedTime(payload?.sourceTimestamp || payload?.refreshedAt);
            setMessage(mapStatus, `${vehicles.length} live vehicle${vehicles.length === 1 ? '' : 's'} updated ${updated}.`, 'success');
          }
        })
        .catch(() => {
          if (mapStatus) {
            setMessage(mapStatus, 'Unable to refresh live vehicle data.', 'error');
          }
        });
    }
    return;
  }

  if (trackingApp) {
    document.body.classList.add('tracking-active');
  }

  if (window.MAPBOX_TOKEN && window.MAPBOX_TOKEN !== 'YOUR_MAPBOX_ACCESS_TOKEN_HERE') {
    mapboxgl.accessToken = window.MAPBOX_TOKEN;
    syncMapContainerSize();

    map = new mapboxgl.Map({
      container: 'map',
      style: 'mapbox://styles/mapbox/streets-v12',
      center: boltonCenter,
      zoom: 10.6,
    });
    mapAvailable = true;

    const vehicleStates = new Map();
    const vehicleDataById = new Map();
    let selectedVehicleId = null;
    let selectedVehicleFleet = null;
    let followSelectedVehicle = false;
    let refreshIntervalId = null;
    let stopFeatureCollection = { type: 'FeatureCollection', features: [] };
    let stopFeaturesLoaded = false;
    const routeSourceId = 'gnw-route-overlay-source';
    const routeOutlineLayerId = 'gnw-route-overlay-outline';
    const routeLayerId = 'gnw-route-overlay';
    const stopSourceId = 'gnw-stop-overlay-source';
    const stopLayerId = 'gnw-stop-overlay';
    const stopHitLayerId = 'gnw-stop-overlay-hit-area';
    let stopInteractionsBound = false;
    let lastStopClickAt = 0;
    let selectedStopIdentifier = null;
    let stopDetailsRefreshIntervalId = null;
    const emptyRouteFeatureCollection = { type: 'FeatureCollection', features: [] };
    const emptyStopFeatureCollection = { type: 'FeatureCollection', features: [] };

    const normalizeFleetKey = (fleetNumber) => String(fleetNumber || '').trim().toLowerCase();

    const flashingJourneyNumbers = new Set([
      '8001', '8002', '8301', '8302', '8601', '8602',
      '1001', '1002', '1301', '1302', '1601', '1602',
    ]);

    const isFlashingJourney = (vehicle) => {
      const journey = formatJourneyNumber(vehicle);
      return flashingJourneyNumbers.has(String(journey || '').trim());
    };

    const directionBadgeMarkup = (direction) => {
      const normalized = String(direction || '').trim().toLowerCase();
      if (normalized === 'inbound') {
        return '<span class="vehicle-direction-badge inbound">[I]</span>';
      }
      if (normalized === 'outbound') {
        return '<span class="vehicle-direction-badge outbound">[O]</span>';
      }
      return '<span class="vehicle-direction-badge unknown">[?]</span>';
    };

    const buildVehicleFlagMarkup = (vehicle) => {
      const service = escapeHtml(formatRouteLabel(vehicle));
      const destination = escapeHtml(String(vehicle?.destination || 'Unknown destination').trim() || 'Unknown destination');
      const board = escapeHtml(formatBoardNumber(vehicle));
      return `
        <span class="vehicle-flag-line service-line">${service} ${directionBadgeMarkup(vehicle?.direction)}</span>
        <span class="vehicle-flag-line destination-line">${destination}</span>
        <span class="vehicle-flag-line board-line">RB ${board}</span>
      `;
    };

    const setRouteStatus = (message) => {
      if (routeStatus) {
        routeStatus.textContent = message;
      }
    };

    const centerVehicleOnMap = (vehicle, immediate = false) => {
      if (!mapAvailable || !map || !vehicle) return;
      const lng = Number(vehicle.longitude);
      const lat = Number(vehicle.latitude);
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) return;
      const zoom = Math.max(14.5, Number(map.getZoom?.() || 14.5));
      map.easeTo({
        center: [lng, lat],
        zoom,
        duration: immediate ? 0 : 650,
      });
    };

    const syncFollowButton = () => {
      if (!trackingFollowToggle) return;
      trackingFollowToggle.classList.toggle('is-following', followSelectedVehicle);
      trackingFollowToggle.setAttribute('aria-pressed', followSelectedVehicle ? 'true' : 'false');
      trackingFollowToggle.textContent = followSelectedVehicle ? 'Following vehicle' : 'Follow vehicle';
    };

    const executeVehicleSearch = () => {
      const query = String(trackingSearchQuery?.value || '').trim().toLowerCase();
      const searchType = String(trackingSearchType?.value || 'fleetNumber');
      if (!query) {
        setMessage(mapStatus, 'Enter a fleet or running board number to search.', 'error');
        return;
      }

      const vehicles = Array.from(vehicleDataById.values());
      const getSearchValue = (vehicle) => {
        if (searchType === 'runningBoard') {
          // Match what users see in the sidebar board field.
          return formatBoardNumber(vehicle).toLowerCase();
        }
        return String(vehicle?.fleetNumber || '').trim().toLowerCase();
      };

      const exactMatch = vehicles.find((vehicle) => getSearchValue(vehicle) === query);
      let match = exactMatch || null;
      if (!match) {
        const partialMatches = vehicles.filter((vehicle) => getSearchValue(vehicle).includes(query));
        if (partialMatches.length === 1) {
          match = partialMatches[0];
        }
      }

      if (!match || !match.id) {
        setMessage(mapStatus, 'No matching vehicle found.', 'error');
        return;
      }

      selectVehicle(match.id, { enableFollow: true, center: true });
      setMessage(mapStatus, `Tracking fleet ${match.fleetNumber}.`, 'success');
    };

    const removeVehicleMarker = (state) => {
      if (!state.marker) {
        return;
      }
      state.marker.remove();
      state.marker = null;
      state.flag = null;
      state.element = null;
    };

    const syncSelectedMarkerStyles = () => {
      vehicleStates.forEach((state, vehicleId) => {
        if (state.element) {
          state.element.classList.toggle('is-selected', vehicleId === selectedVehicleId);
        }
      });
    };

    const renderStopArrivalMarkup = (arrivals = []) => {
      if (!Array.isArray(arrivals) || !arrivals.length) {
        return '<li class="muted">No upcoming BNGN services.</li>';
      }

      return arrivals.map((arrival) => {
        const service = String(arrival?.service || arrival?.routeId || arrival?.serviceId || 'Unknown').trim() || 'Unknown';
        const fleet = String(arrival?.fleetNumber || 'Unknown fleet').trim() || 'Unknown fleet';
        const direction = arrival?.direction ? ` ${formatVehicleDirection(arrival.direction)}` : '';
        const countdown = String(arrival?.countdownLabel || 'Due now').trim() || 'Due now';
        const sourceLabel = arrival?.source === 'scheduled' ? ' (Scheduled)' : '';
        return `<li><span class="stop-arrival-service">${escapeHtml(service)} ${escapeHtml(fleet)}${escapeHtml(direction)}</span><span class="stop-arrival-countdown">${escapeHtml(countdown)}${sourceLabel}</span></li>`;
      }).join('');
    };

    const setSidebarEmpty = (message) => {
      if (sidebarEmpty) {
        sidebarEmpty.textContent = message;
        sidebarEmpty.hidden = false;
      }
      if (sidebarPanel) {
        sidebarPanel.hidden = true;
      }
      if (selectedStopPanel) selectedStopPanel.hidden = true;
      selectedStopIdentifier = null;
    };

    const setSidebarStop = (stop) => {
      if (!stop || !selectedStopPanel) {
        return;
      }

      const stopName = String(stop.name || 'Unknown stop').trim() || 'Unknown stop';
      const stopCode = String(stop.naptan || stop.stopCode || stop.id || 'Unknown').trim() || 'Unknown';
      const stopIdValue = String(stop.stopId || stop.id || 'Unknown').trim() || 'Unknown';
      const nextArrivals = Array.isArray(stop.nextArrivals) ? stop.nextArrivals : [];

      if (sidebarEmpty) sidebarEmpty.hidden = true;
      if (sidebarPanel) sidebarPanel.hidden = false;
      selectedStopPanel.hidden = false;
      if (selectedStopName) selectedStopName.textContent = stopName;
      if (selectedStopCode) selectedStopCode.textContent = stopCode;
      if (selectedStopId) selectedStopId.textContent = stopIdValue;
      if (selectedStopArrivals) selectedStopArrivals.innerHTML = renderStopArrivalMarkup(nextArrivals);
    };

    const setSidebarVehicle = (vehicle) => {
      if (!vehicle) {
        setSidebarEmpty('Select a bus marker to inspect its service details.');
        return;
      }

      if (sidebarEmpty) {
        sidebarEmpty.hidden = true;
      }
      if (sidebarPanel) {
        sidebarPanel.hidden = false;
      }
      if (selectedStopPanel) selectedStopPanel.hidden = true;
      selectedStopIdentifier = null;
      const fleetDisplay = String(vehicle.fleetNumber || 'Unknown').trim() || 'Unknown';
      if (selectedService) selectedService.textContent = fleetDisplay;
      if (selectedRoute) selectedRoute.textContent = formatRouteLabel(vehicle);
      if (selectedFleet) selectedFleet.textContent = fleetDisplay;
      if (selectedDirection) selectedDirection.textContent = formatVehicleDirection(vehicle.direction);
      if (selectedDirectionLabel) selectedDirectionLabel.textContent = formatVehicleDirection(vehicle.direction);
      if (selectedDestination) selectedDestination.textContent = String(vehicle.destination || 'Unknown').trim() || 'Unknown';
      if (selectedBoard) selectedBoard.textContent = formatBoardNumber(vehicle);
      const journeyNumber = formatJourneyNumber(vehicle);
      if (selectedJourney) {
        selectedJourney.textContent = journeyNumber;
        selectedJourney.classList.toggle('journey-flash', isFlashingJourney(vehicle));
      }
      if (selectedPunctuality) {
        const punctuality = vehicle?.punctuality || {};
        const punctualityLabel = punctuality.label || 'Unknown';
        selectedPunctuality.textContent = punctualityLabel;
        selectedPunctuality.className = `sidebar-pill punctuality-pill ${punctuality.tone || 'neutral'}`;
      }
      if (selectedOriginDeparture) selectedOriginDeparture.textContent = formatJourneyOriginDeparture(vehicle);
      if (selectedLastStop) selectedLastStop.textContent = formatLastStop(vehicle.lastStopPassed);
      if (selectedUpdated) selectedUpdated.textContent = `Updated ${formatFeedTime(vehicle.recordedAt || vehicle.sourceTimestamp || vehicle.refreshedAt)}`;
    };

    const selectVehicle = (vehicleId, options = {}) => {
      const enableFollow = Boolean(options.enableFollow);
      const shouldCenter = options.center !== false;

      selectedVehicleId = vehicleId;
      if (!vehicleId) {
        selectedVehicleFleet = null;
        followSelectedVehicle = false;
        syncFollowButton();
        setSidebarEmpty('Select a bus marker to inspect its service details.');
        syncSelectedMarkerStyles();
        return;
      }
      const selectedVehicle = vehicleDataById.get(vehicleId) || null;
      selectedVehicleFleet = normalizeFleetKey(selectedVehicle?.fleetNumber);
      if (enableFollow) {
        followSelectedVehicle = true;
      }
      syncFollowButton();
      setSidebarVehicle(selectedVehicle);
      if (selectedVehicle && shouldCenter) {
        centerVehicleOnMap(selectedVehicle);
      }
      syncSelectedMarkerStyles();
    };

    const ensureVehicleMarker = (state, lngLat, vehicle) => {
      const direction = String(vehicle.direction || 'unknown').trim().toLowerCase();
      const fleetDisplay = String(vehicle.fleetNumber || 'Unknown').trim() || 'Unknown';
      if (state.marker) {
        state.marker.setLngLat(lngLat);
        state.flag.dataset.direction = direction;
        state.flag.innerHTML = buildVehicleFlagMarkup(vehicle);
        state.pin.textContent = fleetDisplay;
        state.element.classList.toggle('is-flashing-journey', isFlashingJourney(vehicle));
        state.element.classList.toggle('is-selected', vehicle.id === selectedVehicleId);
        state.data = vehicle;
        return;
      }

      const markerElement = document.createElement('div');
      markerElement.className = 'vehicle-marker';
      markerElement.classList.toggle('is-flashing-journey', isFlashingJourney(vehicle));

      const flag = document.createElement('div');
      flag.className = 'vehicle-flag';
      flag.dataset.direction = direction;
      flag.innerHTML = buildVehicleFlagMarkup(vehicle);

      const pin = document.createElement('div');
      pin.className = 'vehicle-pin';
      pin.textContent = fleetDisplay;

      markerElement.append(flag, pin);
      markerElement.addEventListener('click', (event) => {
        event.stopPropagation();
        selectVehicle(vehicle.id);
      });

      state.flag = flag;
      state.pin = pin;
      state.element = markerElement;
      state.data = vehicle;
      state.marker = new mapboxgl.Marker({ element: markerElement, anchor: 'bottom' }).setLngLat(lngLat).addTo(map);
    };

    const applyZoomStyling = () => {
      const zoom = map.getZoom();
      const normalized = Math.max(0, Math.min(1, (zoom - 9.5) / 4.5));
      const flagScale = 0.3 + normalized * 0.7;
      const flagOpacity = 0.18 + normalized * 0.82;
      vehicleStates.forEach((state) => {
        if (state.element) {
          state.element.style.setProperty('--vehicle-flag-scale', flagScale.toFixed(2));
          state.element.style.setProperty('--vehicle-flag-opacity', flagOpacity.toFixed(2));
        }
      });
    };

    const setRouteControlsEnabled = (enabled) => {
      if (routeSelect) routeSelect.disabled = !enabled;
      if (directionSelect) directionSelect.disabled = !enabled;
    };

    const ensureRouteOverlayLayers = () => {
      if (map.getSource(routeSourceId)) return;
      map.addSource(routeSourceId, { type: 'geojson', data: emptyRouteFeatureCollection });
      map.addLayer({ id: routeOutlineLayerId, type: 'line', source: routeSourceId, paint: { 'line-color': '#07121b', 'line-width': 8, 'line-opacity': 0.88 } });
      map.addLayer({ id: routeLayerId, type: 'line', source: routeSourceId, paint: { 'line-color': ['match', ['get', 'direction'], 'inbound', '#23c36b', 'outbound', '#d43f3a', '#35deff'], 'line-width': 4, 'line-opacity': 0.95 } });
    };

    const ensureStopOverlayLayers = () => {
      if (!map.getSource(stopSourceId)) {
        map.addSource(stopSourceId, { type: 'geojson', data: emptyStopFeatureCollection });
        map.addLayer({ id: stopHitLayerId, type: 'circle', source: stopSourceId, paint: { 'circle-radius': 16, 'circle-color': '#ffffff', 'circle-opacity': 0.01 } });
        map.addLayer({ id: stopLayerId, type: 'circle', source: stopSourceId, paint: { 'circle-color': '#5fc1ff', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 2.4, 12, 3.8, 15, 5.2], 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1, 'circle-opacity': 0.55 } });
      }
      if (stopInteractionsBound) return;

      map.on('click', stopHitLayerId, (event) => {
        const feature = event.features?.[0];
        if (feature) {
          lastStopClickAt = Date.now();
          selectStop(feature.properties || {});
        }
      });
      map.on('mouseenter', stopHitLayerId, () => {
        map.getCanvas().style.cursor = 'pointer';
      });
      map.on('mouseleave', stopHitLayerId, () => {
        map.getCanvas().style.cursor = '';
      });
      stopInteractionsBound = true;
    };

    const applyRouteOverlay = (featureCollection, showOverlay) => {
      if (!canRenderMapOverlays()) return;
      ensureRouteOverlayLayers();
      const source = map.getSource(routeSourceId);
      if (!source) return;
      source.setData(showOverlay ? featureCollection : emptyRouteFeatureCollection);
      const visibility = showOverlay ? 'visible' : 'none';
      if (map.getLayer(routeOutlineLayerId)) map.setLayoutProperty(routeOutlineLayerId, 'visibility', visibility);
      if (map.getLayer(routeLayerId)) map.setLayoutProperty(routeLayerId, 'visibility', visibility);
    };

    const applyStopOverlay = (featureCollection, showOverlay) => {
      if (!canRenderMapOverlays()) return;
      ensureStopOverlayLayers();
      const source = map.getSource(stopSourceId);
      if (!source) return;
      source.setData(showOverlay ? featureCollection : emptyStopFeatureCollection);
      const visibility = showOverlay ? 'visible' : 'none';
      if (map.getLayer(stopHitLayerId)) map.setLayoutProperty(stopHitLayerId, 'visibility', visibility);
      if (map.getLayer(stopLayerId)) map.setLayoutProperty(stopLayerId, 'visibility', visibility);
    };

    const updateRouteOptions = (routes, selectedRouteValue) => {
      if (!routeSelect) return;
      const currentSelection = selectedRouteValue || routeSelect.value || '';
      routeSelect.innerHTML = ['<option value="">Select a service to display</option>', ...routes.map((route) => `<option value="${escapeHtml(route.id || '')}">${escapeHtml(route.label || route.lineName || route.id || 'Route')}</option>`)].join('');
      const routeIds = routes.map((route) => String(route.id || ''));
      routeSelect.value = routeIds.includes(currentSelection) ? currentSelection : '';
    };

    const loadTrackingStops = async () => {
      if (!window.OCC_ASSIST.trackingStopsUrl) return;
      try {
        const response = await fetch(window.OCC_ASSIST.trackingStopsUrl, { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.message || 'Unable to load stop data.');
        const stops = payload.stops || [];
        stopFeatureCollection = {
          type: 'FeatureCollection',
          features: stops.map((stop) => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [stop.longitude, stop.latitude] },
            properties: {
              stopId: stop.id,
              id: stop.id,
              naptan: stop.naptan || stop.stopCode || stop.id,
              stopCode: stop.stopCode || stop.naptan || stop.id,
              name: stop.name,
              nextArrivals: stop.nextArrivals || [],
            },
          })),
        };
        stopFeaturesLoaded = true;
        if (stopToggle) stopToggle.disabled = false;
        applyStopOverlay(stopFeatureCollection, Boolean(stopToggle?.checked));
      } catch (error) {
        stopFeatureCollection = emptyStopFeatureCollection;
        stopFeaturesLoaded = false;
        applyStopOverlay(emptyStopFeatureCollection, false);
        if (stopToggle) stopToggle.disabled = true;
      }
    };

    const selectStop = (stopProperties) => {
      if (!stopProperties) {
        return;
      }
      const normalizedStop = {
        id: stopProperties.id || stopProperties.stopId || '',
        stopId: stopProperties.stopId || stopProperties.id || '',
        name: stopProperties.name || 'Unknown stop',
        naptan: stopProperties.naptan || stopProperties.stopCode || stopProperties.id || '',
        stopCode: stopProperties.stopCode || stopProperties.naptan || stopProperties.id || '',
        nextArrivals: Array.isArray(stopProperties.nextArrivals) ? stopProperties.nextArrivals : [],
      };
      selectedStopIdentifier = normalizedStop.stopId;
      setSidebarStop(normalizedStop);
      refreshSelectedStopDetails();
      if (stopDetailsRefreshIntervalId === null) {
        stopDetailsRefreshIntervalId = window.setInterval(refreshSelectedStopDetails, 10000);
      }
    };

    const refreshSelectedStopDetails = () => {
      const stopId = String(selectedStopIdentifier || '').trim();
      if (!stopId || !window.OCC_ASSIST.trackingStopsUrl) return;
      fetch(`${window.OCC_ASSIST.trackingStopsUrl}/${encodeURIComponent(stopId)}`, { cache: 'no-store' })
        .then((response) => response.json())
        .then((payload) => {
          if (payload?.ok && payload.stop) setSidebarStop(payload.stop);
        })
        .catch(() => {});
    };

    const renderVehicles = (vehicles, observedAtMs) => {
      const activeIds = new Set();
      let visibleVehicleCount = 0;
      vehicles.forEach((vehicle) => {
        activeIds.add(vehicle.id);
        vehicleDataById.set(vehicle.id, vehicle);
        const lngLat = [vehicle.longitude, vehicle.latitude];
        if (vehicleStates.has(vehicle.id)) {
          const vehicleState = vehicleStates.get(vehicle.id);
          ensureVehicleMarker(vehicleState, lngLat, vehicle);
          visibleVehicleCount += 1;
          return;
        }
        const vehicleState = { marker: null, flag: null, pin: null, element: null, data: vehicle };
        ensureVehicleMarker(vehicleState, lngLat, vehicle);
        vehicleStates.set(vehicle.id, vehicleState);
        visibleVehicleCount += 1;
      });
      vehicleStates.forEach((vehicleState, vehicleId) => {
        if (!activeIds.has(vehicleId)) {
          removeVehicleMarker(vehicleState);
          vehicleStates.delete(vehicleId);
          vehicleDataById.delete(vehicleId);
        }
      });
      if (selectedVehicleId && !vehicleDataById.has(selectedVehicleId) && selectedVehicleFleet) {
        const reassigned = vehicles.find((vehicle) => normalizeFleetKey(vehicle.fleetNumber) === selectedVehicleFleet);
        if (reassigned && reassigned.id) {
          selectedVehicleId = reassigned.id;
        }
      }

      if (selectedVehicleId && !vehicleDataById.has(selectedVehicleId)) {
        selectedVehicleId = null;
        selectedVehicleFleet = null;
        setSidebarEmpty('Select a bus marker to inspect its service details.');
      } else if (selectedVehicleId) {
        const selectedVehicle = vehicleDataById.get(selectedVehicleId);
        selectedVehicleFleet = normalizeFleetKey(selectedVehicle?.fleetNumber);
        setSidebarVehicle(selectedVehicle);
        if (followSelectedVehicle && selectedVehicle) {
          centerVehicleOnMap(selectedVehicle);
        }
      }
      syncSelectedMarkerStyles();
      return visibleVehicleCount;
    };

    const refreshVehicles = async () => {
      if (!window.OCC_ASSIST.trackingVehiclesUrl) return;
      try {
        const response = await fetch(window.OCC_ASSIST.trackingVehiclesUrl, { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.message || 'Unable to load vehicle positions.');
        const observedAtMs = Date.parse(payload.sourceTimestamp || payload.refreshedAt) || Date.now();
        const activeVehicles = payload.vehicles || [];
        const visibleVehicleCount = renderVehicles(activeVehicles, observedAtMs);
        const updated = formatFeedTime(payload.sourceTimestamp || payload.refreshedAt);
        setMessage(mapStatus, `${visibleVehicleCount} live vehicle${visibleVehicleCount === 1 ? '' : 's'} updated ${updated}.`, 'success');
      } catch (error) {
        setMessage(mapStatus, error.message || 'Unable to load vehicle positions.', 'error');
      }
    };

    const loadStaticRoutes = async () => {
      if (!window.OCC_ASSIST.trackingStaticRoutesUrl) {
        setRouteStatus('Static route API is not configured.');
        return;
      }
      try {
        const selectedRouteValue = String(routeSelect?.value || '').trim();
        const selectedDirectionValue = directionSelect?.value || 'all';
        const routeForFetch = selectedRouteValue || 'all';
        const query = new URLSearchParams({ route: routeForFetch, direction: selectedDirectionValue });
        const response = await fetch(`${window.OCC_ASSIST.trackingStaticRoutesUrl}?${query.toString()}`, { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.message || 'Unable to load static route data.');

        const routes = payload.routes || [];
        updateRouteOptions(routes, selectedRouteValue);
        if (directionSelect) directionSelect.value = payload.selectedDirection || selectedDirectionValue;

        const overlayRequested = Boolean(routeToggle?.checked);
        const hasRouteSelection = Boolean(routeSelect?.value);
        const overlayVisible = overlayRequested && payload.configured && hasRouteSelection;
        const overlayData = hasRouteSelection ? (payload.featureCollection || emptyRouteFeatureCollection) : emptyRouteFeatureCollection;

        applyRouteOverlay(overlayData, overlayVisible);
        setRouteControlsEnabled(overlayRequested && payload.configured);
        if (directionSelect) {
          directionSelect.disabled = !(overlayRequested && payload.configured && hasRouteSelection);
        }

        if (!payload.configured) {
          setRouteStatus(payload.message || 'No GTFS ZIP has been uploaded yet.');
          return;
        }

        if (!overlayRequested) {
          setRouteStatus(`${payload.routeCount} route${payload.routeCount === 1 ? '' : 's'} loaded. Enable overlay to display paths.`);
          return;
        }

        if (!hasRouteSelection) {
          setRouteStatus(`${payload.routeCount} route${payload.routeCount === 1 ? '' : 's'} loaded. Select a service to display its path.`);
          return;
        }

        const selected = routeSelect?.value || '';
        const directionLabel = selectedDirectionValue === 'inbound' ? 'Showing inbound trips only.' : selectedDirectionValue === 'outbound' ? 'Showing outbound trips only.' : 'Showing inbound and outbound trips.';
        setRouteStatus(`${payload.routeCount} route${payload.routeCount === 1 ? '' : 's'} loaded. Showing route ${selected}. ${directionLabel}`);
      } catch (error) {
        applyRouteOverlay(emptyRouteFeatureCollection, false);
        setRouteControlsEnabled(false);
        if (directionSelect) directionSelect.disabled = true;
        setRouteStatus(error.message || 'Unable to load static route data.');
      }
    };

    const startVehicleRefresh = () => {
      if (refreshIntervalId !== null) return;
      setMessage(mapStatus, 'Loading vehicle positions...', 'success');
      refreshVehicles();
      refreshIntervalId = window.setInterval(refreshVehicles, 7000);
    };

    if (map.loaded()) {
      initializeOverlays();
    } else {
      map.once('load', initializeOverlays);
      window.setTimeout(startVehicleRefresh, 1500);
    }

    window.requestAnimationFrame(() => {
      resizeMap();
      window.requestAnimationFrame(resizeMap);
    });
    window.setTimeout(resizeMap, 250);
    window.addEventListener('resize', resizeMap);

    applyZoomStyling();
    map.on('zoom', applyZoomStyling);
    map.on('click', () => {
      if (Date.now() - lastStopClickAt < 100) return;
      selectVehicle(null);
    });

    setRouteControlsEnabled(false);
    setRouteStatus('Load a GTFS ZIP from Admin to display static route paths.');
    if (sidebarEmpty) setSidebarEmpty('Select a bus marker to inspect its service details.');

    if (routeToggle) {
      routeToggle.addEventListener('change', () => {
        if (!routeToggle.checked) {
          setRouteControlsEnabled(false);
          applyRouteOverlay(emptyRouteFeatureCollection, false);
          loadStaticRoutes();
          return;
        }
        loadStaticRoutes();
      });
    }

    if (routeSelect) routeSelect.addEventListener('change', () => loadStaticRoutes());
    if (directionSelect) directionSelect.addEventListener('change', () => loadStaticRoutes());
    if (stopToggle) {
      stopToggle.addEventListener('change', () => {
        if (!stopFeaturesLoaded) {
          loadTrackingStops();
          return;
        }
        applyStopOverlay(stopFeatureCollection, stopToggle.checked);
      });
    }

    if (trackingSearchSubmit) {
      trackingSearchSubmit.addEventListener('click', executeVehicleSearch);
    }
    if (trackingSearchQuery) {
      trackingSearchQuery.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          executeVehicleSearch();
        }
      });
    }
    if (trackingFollowToggle) {
      trackingFollowToggle.addEventListener('click', () => {
        if (!selectedVehicleId) {
          followSelectedVehicle = false;
          syncFollowButton();
          setMessage(mapStatus, 'Select a vehicle first, then enable follow.', 'error');
          return;
        }
        followSelectedVehicle = !followSelectedVehicle;
        syncFollowButton();
        if (followSelectedVehicle) {
          const vehicle = vehicleDataById.get(selectedVehicleId);
          if (vehicle) {
            centerVehicleOnMap(vehicle);
          }
        }
      });
    }
    syncFollowButton();

    return;
  }

  mapContainer.innerHTML = '<div class="placeholder-card"><p>Mapbox token is not configured yet.</p></div>';
}


function initializeServiceOverview() {
  const app = document.querySelector('#service-overview-app');
  const refreshButton = document.querySelector('#refresh-service-overview');
  const overviewStatus = document.querySelector('#service-overview-status');
  const routeCountEl = document.querySelector('#service-overview-route-count');
  const vehicleCountEl = document.querySelector('#service-overview-vehicle-count');
  const updatedEl = document.querySelector('#service-overview-updated');
  const listEl = document.querySelector('#service-overview-list');

  if (!app || !refreshButton || !overviewStatus || !routeCountEl || !vehicleCountEl || !updatedEl || !listEl) {
    return;
  }

  const renderOverview = (vehicles, sourceTimestamp) => {
    const groups = new Map();
    vehicles.forEach((vehicle) => {
      const key = normalizeTrackingKey(vehicle.routeId || vehicle.routeLabel || vehicle.service || 'unknown');
      if (!groups.has(key)) {
        groups.set(key, {
          routeId: vehicle.routeId || vehicle.service || 'Unknown',
          routeLabel: formatRouteLabel(vehicle),
          vehicles: [],
        });
      }
      groups.get(key).vehicles.push(vehicle);
    });

    const orderedGroups = Array.from(groups.values()).sort((left, right) => left.routeLabel.localeCompare(right.routeLabel, undefined, { numeric: true, sensitivity: 'base' }));
    routeCountEl.textContent = String(orderedGroups.length);
    vehicleCountEl.textContent = String(vehicles.length);
    updatedEl.textContent = formatFeedTime(sourceTimestamp);

    if (!vehicles.length) {
      listEl.innerHTML = '<p class="saved-empty">No active services are visible right now.</p>';
      return;
    }

    listEl.innerHTML = orderedGroups.map((group) => {
      const routeVehicleCount = group.vehicles.length;
      const routeVehicles = group.vehicles.map((vehicle) => `
        <article class="service-card">
          <div class="service-card-head">
            <div>
              <p class="service-card-route">${escapeHtml(formatRouteLabel(vehicle))}</p>
              <strong>${escapeHtml(String(vehicle.destination || 'Unknown destination'))}</strong>
            </div>
            <span class="sidebar-pill">${escapeHtml(formatVehicleDirection(vehicle.direction))}</span>
          </div>
          <dl class="service-detail-list">
            <div><dt><span class="label-with-icon"><svg viewBox="0 0 24 24" class="info-icon icon-fleet" aria-hidden="true"><rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M8 9h8"/><path d="M8 12h8"/><path d="M8 15h5"/></svg><span>Fleet number</span></span></dt><dd>${escapeHtml(String(vehicle.fleetNumber || 'Unknown'))}</dd></div>
            <div><dt><span class="label-with-icon"><svg viewBox="0 0 24 24" class="info-icon icon-journey" aria-hidden="true"><path d="M7 4h10"/><path d="M6 7h12"/><rect x="5" y="4" width="14" height="16" rx="2"/><path d="M9 11h6"/><path d="M9 14h4"/></svg><span>Journey number</span></span></dt><dd>${escapeHtml(formatJourneyNumber(vehicle))}</dd></div>
            <div><dt><span class="label-with-icon"><svg viewBox="0 0 24 24" class="info-icon icon-time" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg><span>Departed first stop</span></span></dt><dd>${escapeHtml(formatJourneyOriginDeparture(vehicle))}</dd></div>
            <div><dt><span class="label-with-icon"><svg viewBox="0 0 24 24" class="info-icon icon-board" aria-hidden="true"><rect x="6" y="5" width="12" height="16" rx="2"/><path d="M9 5.5h6v3H9z"/><path d="M9 12h6"/><path d="M9 15h4"/></svg><span>Board number</span></span></dt><dd>${escapeHtml(formatBoardNumber(vehicle))}</dd></div>
            <div><dt><span class="label-with-icon"><svg viewBox="0 0 24 24" class="info-icon icon-time" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg><span>Early / late</span></span></dt><dd><span class="sidebar-pill punctuality-pill ${escapeHtml(vehicle?.punctuality?.tone || 'neutral')}">${escapeHtml(vehicle?.punctuality?.label || 'Unknown')}</span></dd></div>
            <div><dt><span class="label-with-icon"><svg viewBox="0 0 24 24" class="info-icon icon-stop" aria-hidden="true"><path d="M12 21s6-4.5 6-10a6 6 0 1 0-12 0c0 5.5 6 10 6 10z"/><path d="M10 9h4l-1.4 1.8L14 13h-4"/></svg><span>Last stop passed</span></span></dt><dd>${escapeHtml(formatLastStop(vehicle.lastStopPassed))}</dd></div>
          </dl>
        </article>
      `).join('');
      return `
        <section class="service-group" data-route-group="${escapeHtml(group.routeId)}">
          <header class="service-group-head">
            <div>
              <p class="brand-subtitle">Route ${escapeHtml(group.routeLabel)}</p>
              <h2>${escapeHtml(group.routeLabel)}</h2>
            </div>
            <div class="service-group-actions">
              <span class="service-count-pill">${routeVehicleCount} active</span>
              <button type="button" class="service-group-toggle" aria-expanded="true" aria-label="Collapse route ${escapeHtml(group.routeLabel)}">
                <span class="chevron">▾</span>
              </button>
            </div>
          </header>
          <div class="service-group-list">${routeVehicles}</div>
        </section>
      `;
    }).join('');

    listEl.querySelectorAll('.service-group').forEach((group) => {
      group.classList.add('is-collapsed');
      const toggle = group.querySelector('.service-group-toggle');
      if (toggle) {
        toggle.setAttribute('aria-expanded', 'false');
      }
    });
  };

  const refreshOverview = async () => {
    if (!window.OCC_ASSIST.trackingVehiclesUrl) {
      setMessage(overviewStatus, 'Vehicle API is not configured.', 'error');
      return;
    }
    try {
      const response = await fetch(window.OCC_ASSIST.trackingVehiclesUrl, { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || 'Unable to load active services.');
      renderOverview(payload.vehicles || [], payload.sourceTimestamp || payload.refreshedAt);
      setMessage(overviewStatus, `Loaded ${payload.vehicles?.length || 0} active vehicle${(payload.vehicles?.length || 0) === 1 ? '' : 's'}.`, 'success');
    } catch (error) {
      setMessage(overviewStatus, error.message || 'Unable to load active services.', 'error');
      listEl.innerHTML = '<p class="saved-empty">Unable to load active services right now.</p>';
    }
  };

  listEl.addEventListener('click', (event) => {
    const toggle = event.target.closest('.service-group-toggle');
    if (!toggle) {
      return;
    }

    const group = toggle.closest('.service-group');
    if (!group) {
      return;
    }

    const willCollapse = !group.classList.contains('is-collapsed');
    group.classList.toggle('is-collapsed', willCollapse);
    toggle.setAttribute('aria-expanded', willCollapse ? 'false' : 'true');
  });

  refreshButton.addEventListener('click', refreshOverview);
  refreshOverview();
}

function formatFeedTime(value) {
  if (!value) {
    return 'just now';
  }

  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return 'just now';
  }

  return timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function initializeDrivingHours() {
  const app = document.querySelector('#driving-hours-app');
  const segmentForm = document.querySelector('#segment-form');
  const segmentList = document.querySelector('#segment-list');
  const clearButton = document.querySelector('#clear-segments');
  const cancelEditButton = document.querySelector('#cancel-segment-edit');
  const formMessage = document.querySelector('#segment-message');
  const metricsPanel = document.querySelector('#hours-metrics');
  const alertsPanel = document.querySelector('#hours-alerts');
  const saveSnapshotButton = document.querySelector('#save-snapshot');
  const savedSnapshotsPanel = document.querySelector('#saved-snapshots');
  const savedSummary = document.querySelector('#saved-summary');
  const snapshotSearchInput = document.querySelector('#snapshot-search');
  const activeUserLabel = document.querySelector('#active-user-label');
  const driverNameInput = document.querySelector('#driver-name');
  const employeeNumberInput = document.querySelector('#employee-number');

  if (
    !app || !segmentForm || !segmentList || !clearButton || !cancelEditButton || !metricsPanel || !alertsPanel || !saveSnapshotButton
    || !savedSnapshotsPanel || !savedSummary || !snapshotSearchInput || !driverNameInput || !employeeNumberInput
  ) {
    return;
  }

  document.body.classList.add('driving-hours-active');

  const minutesPerHour = 60;
  const limits = {
    dailyDrivingMinutes: 10 * minutesPerHour,
    spreadoverMinutes: 16 * minutesPerHour,
    breakTriggerDrivingMinutes: 5.5 * minutesPerHour,
    shortBreakMinutes: 30,
    longDayThresholdMinutes: 8.5 * minutesPerHour,
    longDayNonDrivingMinutes: 45,
  };

  let segments = [];
  let snapshots = [];
  let editingSegmentId = null;

  const normalizeForSearch = (value) => String(value || '').toLowerCase().trim();

  const escapeHtml = (value) => {
    return String(value)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  };

  const parseTimeToMinutes = (value) => {
    const [hour, minute] = value.split(':').map((item) => Number(item));
    return hour * minutesPerHour + minute;
  };

  const formatMinutesAsTime = (totalMinutes) => {
    const normalized = ((totalMinutes % 1440) + 1440) % 1440;
    const hour = String(Math.floor(normalized / 60)).padStart(2, '0');
    const minute = String(normalized % 60).padStart(2, '0');
    return `${hour}:${minute}`;
  };

  const formatDuration = (minutes) => {
    const safeMinutes = Math.max(0, Math.round(minutes));
    const hours = Math.floor(safeMinutes / 60);
    const remainingMinutes = safeMinutes % 60;
    return `${hours}h ${String(remainingMinutes).padStart(2, '0')}m`;
  };

  const getOverlapMinutes = (segmentStart, segmentEnd, windowStart, windowEnd) => {
    const start = Math.max(segmentStart, windowStart);
    const end = Math.min(segmentEnd, windowEnd);
    return Math.max(0, end - start);
  };

  const sortSegments = () => {
    segments.sort((left, right) => left.startMinutes - right.startMinutes);
  };

  const hasOverlap = (startMinutes, endMinutes) => {
    return segments.some((segment) => !(endMinutes <= segment.startMinutes || startMinutes >= segment.endMinutes));
  };

  const getContinuousDrivingAtEnd = (orderedSegments) => {
    let continuous = 0;
    for (let index = orderedSegments.length - 1; index >= 0; index -= 1) {
      const segment = orderedSegments[index];
      const duration = segment.endMinutes - segment.startMinutes;
      if (segment.type === 'driving') {
        continuous += duration;
        continue;
      }
      if (duration >= limits.shortBreakMinutes) {
        break;
      }
    }
    return continuous;
  };

  const calculateCompliance = (orderedSegments) => {
    if (orderedSegments.length === 0) {
      return {
        totalDrivingMinutes: 0,
        totalBreakMinutes: 0,
        spreadoverMinutes: 0,
        currentContinuousDrivingMinutes: 0,
        nonDrivingInFirstWindowMinutes: 0,
        breaches: [],
        status: 'compliant',
      };
    }

    const dayStart = orderedSegments[0].startMinutes;
    const dayEnd = orderedSegments[orderedSegments.length - 1].endMinutes;
    const spreadoverMinutes = dayEnd - dayStart;

    let totalDrivingMinutes = 0;
    let totalBreakMinutes = 0;
    let currentSpellDriving = 0;
    let breakRuleAExceeded = false;
    let nonDrivingInFirstWindowMinutes = 0;
    let hasBreak30AfterLongWindow = false;
    const longDayWindowEnd = dayStart + limits.longDayThresholdMinutes;

    orderedSegments.forEach((segment) => {
      const duration = segment.endMinutes - segment.startMinutes;
      if (segment.type === 'driving') {
        totalDrivingMinutes += duration;
        currentSpellDriving += duration;
        if (currentSpellDriving > limits.breakTriggerDrivingMinutes) {
          breakRuleAExceeded = true;
        }
        return;
      }

      totalBreakMinutes += duration;
      nonDrivingInFirstWindowMinutes += getOverlapMinutes(
        segment.startMinutes,
        segment.endMinutes,
        dayStart,
        longDayWindowEnd,
      );

      if (duration >= limits.shortBreakMinutes && segment.startMinutes >= longDayWindowEnd) {
        hasBreak30AfterLongWindow = true;
      }

      if (duration >= limits.shortBreakMinutes) {
        currentSpellDriving = 0;
      }
    });

    const currentContinuousDrivingMinutes = getContinuousDrivingAtEnd(orderedSegments);
    const breaches = [];

    if (totalDrivingMinutes > limits.dailyDrivingMinutes) {
      breaches.push(`Daily driving limit exceeded: ${formatDuration(totalDrivingMinutes)} (limit ${formatDuration(limits.dailyDrivingMinutes)}).`);
    }

    if (spreadoverMinutes > limits.spreadoverMinutes) {
      breaches.push(`Spreadover limit exceeded: ${formatDuration(spreadoverMinutes)} (limit ${formatDuration(limits.spreadoverMinutes)}).`);
    }

    if (spreadoverMinutes < limits.longDayThresholdMinutes) {
      if (breakRuleAExceeded) {
        breaches.push('Break breach: a 30-minute break is required before driving exceeds 5h 30m.');
      }
    } else {
      const optionA = !breakRuleAExceeded;
      const optionB =
        nonDrivingInFirstWindowMinutes >= limits.longDayNonDrivingMinutes && hasBreak30AfterLongWindow;

      if (!optionA && !optionB) {
        breaches.push('Break breach: for days of 8h 30m or more, either take a 30-minute break before 5h 30m driving, or complete 45 minutes non-driving in first 8h 30m and then take a 30-minute break.');
      }
    }

    return {
      totalDrivingMinutes,
      totalBreakMinutes,
      spreadoverMinutes,
      currentContinuousDrivingMinutes,
      nonDrivingInFirstWindowMinutes,
      breaches,
      status: breaches.length ? 'breached' : 'compliant',
    };
  };

  const renderMetrics = (summary) => {
    const metrics = [
      ['Current Driving Before Break', formatDuration(summary.currentContinuousDrivingMinutes)],
      ['Total Driving Today', formatDuration(summary.totalDrivingMinutes)],
      ['Total Break Time', formatDuration(summary.totalBreakMinutes)],
      ['Spreadover', formatDuration(summary.spreadoverMinutes)],
      ['Non-Driving In First 8h 30m', formatDuration(summary.nonDrivingInFirstWindowMinutes)],
    ];

    metricsPanel.innerHTML = metrics
      .map(
        ([label, value]) => `
          <article class="hours-metric">
            <p>${label}</p>
            <strong>${value}</strong>
          </article>
        `,
      )
      .join('');
  };

  const renderAlerts = (summary) => {
    if (summary.breaches.length === 0) {
      alertsPanel.innerHTML = '<div class="hours-alert ok">No GB domestic breaches detected in the current timeline.</div>';
      return;
    }

    alertsPanel.innerHTML = summary.breaches
      .map((message) => `<div class="hours-alert breach">${escapeHtml(message)}</div>`)
      .join('');
  };

  const getFilteredSnapshots = () => {
    const query = normalizeForSearch(snapshotSearchInput.value);
    if (!query) {
      return snapshots;
    }

    return snapshots.filter((snapshot) => {
      const haystack = [
        snapshot.driverName,
        snapshot.employeeNumber,
      ]
        .map((item) => normalizeForSearch(item))
        .join(' ');
      return haystack.includes(query);
    });
  };

  const renderSavedSnapshots = () => {
    if (!snapshots.length) {
      savedSummary.textContent = 'No snapshots saved yet.';
      savedSnapshotsPanel.innerHTML = '<p class="hours-empty">No snapshots saved yet.</p>';
      return;
    }

    const filteredSnapshots = getFilteredSnapshots();
    const totalCount = snapshots.length;
    const filteredCount = filteredSnapshots.length;
    const hasQuery = normalizeForSearch(snapshotSearchInput.value).length > 0;

    savedSummary.textContent = hasQuery
      ? `${filteredCount} of ${totalCount} snapshot${totalCount === 1 ? '' : 's'} shown.`
      : `${totalCount} snapshot${totalCount === 1 ? '' : 's'} stored.`;

    if (!filteredSnapshots.length) {
      savedSnapshotsPanel.innerHTML = '<p class="hours-empty">No snapshots match your search.</p>';
      return;
    }

    savedSnapshotsPanel.innerHTML = filteredSnapshots
      .map((snapshot) => {
        const timestamp = snapshot.createdAtEpoch
          ? new Date(snapshot.createdAtEpoch * 1000)
          : new Date(snapshot.createdAt);
        const statusClass = snapshot.status === 'breached' ? 'breached' : 'compliant';
        const statusLabel = snapshot.status === 'breached' ? 'Breached' : 'Compliant';
        return `
          <article class="saved-item ${statusClass}">
            <header class="saved-head">
              <strong>${escapeHtml(snapshot.driverName)}</strong>
              <span>${escapeHtml(snapshot.employeeNumber)}</span>
              <time>${timestamp.toLocaleString()}</time>
            </header>
            <p class="saved-summary-line">${escapeHtml(snapshot.segmentSummary)}</p>
            <p class="saved-status ${statusClass}">${statusLabel}</p>
          </article>
        `;
      })
      .join('');
  };

  const resetSegmentFormMode = () => {
    editingSegmentId = null;
    const submitButton = segmentForm.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.textContent = 'Add';
    }
    cancelEditButton.hidden = true;
  };

  const setSegmentFormForEdit = (segment) => {
    editingSegmentId = segment.id;
    segmentForm.type.value = segment.type;
    segmentForm.start.value = formatMinutesAsTime(segment.startMinutes);
    segmentForm.end.value = formatMinutesAsTime(segment.endMinutes);
    const submitButton = segmentForm.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.textContent = 'Update';
    }
    cancelEditButton.hidden = false;
    setMessage(formMessage, 'Editing selected segment. Update times/type and click Update.', 'success');
  };

  const renderSegments = () => {
    sortSegments();

    if (segments.length === 0) {
      segmentList.innerHTML = '<tr><td colspan="5" class="hours-empty">No segments logged yet.</td></tr>';
      const emptySummary = calculateCompliance([]);
      renderMetrics(emptySummary);
      renderAlerts(emptySummary);
      return;
    }

    segmentList.innerHTML = segments
      .map((segment) => {
        const duration = segment.endMinutes - segment.startMinutes;
        return `
          <tr>
            <td>${segment.type === 'driving' ? 'Driving' : 'Break'}</td>
            <td>${formatMinutesAsTime(segment.startMinutes)}</td>
            <td>${formatMinutesAsTime(segment.endMinutes)}</td>
            <td>${formatDuration(duration)}</td>
            <td>
              <div class="segment-actions">
                <button class="btn secondary compact" type="button" data-edit-segment="${segment.id}">Edit</button>
                <button class="btn secondary compact" type="button" data-remove-segment="${segment.id}">Remove</button>
              </div>
            </td>
          </tr>
        `;
      })
      .join('');

    const summary = calculateCompliance(segments);
    renderMetrics(summary);
    renderAlerts(summary);
  };

  const loadSnapshots = async () => {
    if (!window.OCC_ASSIST.drivingSnapshotsUrl) {
      return;
    }

    const response = await fetch(window.OCC_ASSIST.drivingSnapshotsUrl, { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || 'Unable to load saved snapshots.');
    }

    snapshots = payload.snapshots || [];
    renderSavedSnapshots();
  };

  segmentForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const type = String(segmentForm.type.value || '').trim();
    const start = String(segmentForm.start.value || '').trim();
    const end = String(segmentForm.end.value || '').trim();

    if (!type || !start || !end) {
      setMessage(formMessage, 'Select a segment type and both times before adding.', 'error');
      return;
    }

    const startMinutes = parseTimeToMinutes(start);
    const endMinutes = parseTimeToMinutes(end);

    if (endMinutes <= startMinutes) {
      setMessage(formMessage, 'End time must be after start time on the same day.', 'error');
      return;
    }

    if (hasOverlap(startMinutes, endMinutes) && !editingSegmentId) {
      setMessage(formMessage, 'This segment overlaps an existing segment. Remove or adjust the overlap first.', 'error');
      return;
    }

    if (editingSegmentId) {
      const existing = segments.find((segment) => segment.id === editingSegmentId);
      if (!existing) {
        resetSegmentFormMode();
        setMessage(formMessage, 'Segment no longer exists. Please add it again.', 'error');
        segmentForm.reset();
        return;
      }

      const overlapsOther = segments.some((segment) => {
        if (segment.id === editingSegmentId) {
          return false;
        }
        return !(endMinutes <= segment.startMinutes || startMinutes >= segment.endMinutes);
      });

      if (overlapsOther) {
        setMessage(formMessage, 'Updated segment overlaps another segment. Adjust the times and try again.', 'error');
        return;
      }

      existing.type = type;
      existing.startMinutes = startMinutes;
      existing.endMinutes = endMinutes;
      renderSegments();
      setMessage(formMessage, 'Segment updated.', 'success');
      segmentForm.reset();
      resetSegmentFormMode();
      return;
    }

    segments.push({
      id: `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
      type,
      startMinutes,
      endMinutes,
    });

    renderSegments();
    setMessage(formMessage, 'Segment added.', 'success');
    segmentForm.reset();
    resetSegmentFormMode();
  });

  segmentList.addEventListener('click', (event) => {
    const trigger = event.target;
    if (!(trigger instanceof HTMLElement)) {
      return;
    }

    const removeId = trigger.getAttribute('data-remove-segment');
    const editId = trigger.getAttribute('data-edit-segment');

    if (editId) {
      const segment = segments.find((item) => item.id === editId);
      if (!segment) {
        setMessage(formMessage, 'Unable to edit that segment right now.', 'error');
        return;
      }
      setSegmentFormForEdit(segment);
      return;
    }

    if (!removeId) {
      return;
    }

    segments = segments.filter((segment) => segment.id !== removeId);
    if (editingSegmentId === removeId) {
      resetSegmentFormMode();
      segmentForm.reset();
    }
    renderSegments();
    setMessage(formMessage, 'Segment removed.', 'success');
  });

  clearButton.addEventListener('click', () => {
    segments = [];
    resetSegmentFormMode();
    segmentForm.reset();
    renderSegments();
    setMessage(formMessage, 'All segments cleared.', 'success');
  });

  cancelEditButton.addEventListener('click', () => {
    resetSegmentFormMode();
    segmentForm.reset();
    setMessage(formMessage, 'Edit cancelled.', 'success');
  });

  snapshotSearchInput.addEventListener('input', () => {
    renderSavedSnapshots();
  });

  saveSnapshotButton.addEventListener('click', async () => {
    const driverName = driverNameInput.value.trim();
    const employeeNumber = employeeNumberInput.value.trim();

    if (!driverName || !employeeNumber) {
      setMessage(formMessage, 'Enter the driver name and employee number before saving.', 'error');
      return;
    }

    if (!segments.length) {
      setMessage(formMessage, 'Add at least one segment before saving.', 'error');
      return;
    }

    const confirmed = window.confirm('Confirm the entries are correct and save this snapshot?');
    if (!confirmed) {
      return;
    }

    const orderedSegments = [...segments]
      .sort((left, right) => left.startMinutes - right.startMinutes)
      .map((segment) => ({
        type: segment.type,
        start: formatMinutesAsTime(segment.startMinutes),
        end: formatMinutesAsTime(segment.endMinutes),
      }));

    const response = await fetch(window.OCC_ASSIST.drivingSnapshotsUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        driverName,
        employeeNumber,
        segments: orderedSegments,
      }),
    });
    const payload = await response.json();

    if (!response.ok || !payload.ok) {
      setMessage(formMessage, payload.message || 'Unable to save snapshot.', 'error');
      return;
    }

    snapshots.unshift(payload.snapshot);
    renderSavedSnapshots();
    setMessage(formMessage, 'Snapshot saved to your account.', 'success');
  });

  const currentUserEmail = window.OCC_ASSIST.currentUser?.email;
  if (currentUserEmail && activeUserLabel) {
    activeUserLabel.textContent = `Signed in as ${currentUserEmail}`;
  }

  renderSegments();
  resetSegmentFormMode();
  loadSnapshots().catch((error) => {
    setMessage(formMessage, error.message || 'Unable to load saved snapshots.', 'error');
    renderSavedSnapshots();
  });
}

function initializeDailyOverview() {
  const overviewRoot = document.querySelector('#daily-overview');
  const refreshButton = document.querySelector('#refresh-overview');
  const overviewMessage = document.querySelector('#overview-message');
  const toggleUpcomingButton = document.querySelector('#toggle-upcoming');
  const upcomingControls = document.querySelector('#upcoming-controls');
  const upcomingScope = document.querySelector('#upcoming-scope');
  const upcomingIncludeRest = document.querySelector('#upcoming-include-rest');
  const upcomingPrev = document.querySelector('#upcoming-prev');
  const upcomingNext = document.querySelector('#upcoming-next');
  const upcomingRefresh = document.querySelector('#upcoming-refresh');
  const upcomingPeriod = document.querySelector('#upcoming-period');
  const upcomingMessage = document.querySelector('#upcoming-message');
  const upcomingList = document.querySelector('#upcoming-list');
  const currentStatus = document.querySelector('#current-shift-status');
  const currentWindow = document.querySelector('#current-shift-window');
  const currentLocation = document.querySelector('#current-shift-location');
  const nextStatus = document.querySelector('#next-shift-status');
  const nextWindow = document.querySelector('#next-shift-window');
  const nextLocation = document.querySelector('#next-shift-location');

  if (
    !overviewRoot || !refreshButton || !overviewMessage || !currentStatus || !currentWindow || !currentLocation
    || !nextStatus || !nextWindow || !nextLocation || !toggleUpcomingButton || !upcomingControls
    || !upcomingScope || !upcomingIncludeRest || !upcomingPrev || !upcomingNext || !upcomingRefresh || !upcomingPeriod
    || !upcomingMessage || !upcomingList
  ) {
    return;
  }

  let upcomingVisible = false;
  let upcomingOffset = 0;

  const escapeHtml = (value) => String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  const renderShiftCard = (target, shift, emptyLabel) => {
    target.status.textContent = shift ? shift.summary : emptyLabel;
    target.window.textContent = shift ? shift.windowLabel : '';
    target.location.textContent = shift && shift.location ? shift.location : '';
  };

  const loadOverview = async () => {
    setMessage(overviewMessage, 'Loading rota shifts...');
    const response = await fetch(window.OCC_ASSIST.overviewShiftsUrl, { cache: 'no-store' });
    const payload = await response.json();

    if (!response.ok || !payload.ok) {
      setMessage(overviewMessage, payload.message || 'Unable to load rota shifts right now.', 'error');
      renderShiftCard(
        { status: currentStatus, window: currentWindow, location: currentLocation },
        null,
        'Unavailable',
      );
      renderShiftCard(
        { status: nextStatus, window: nextWindow, location: nextLocation },
        null,
        'Unavailable',
      );
      return;
    }

    if (!payload.configured) {
      setMessage(overviewMessage, 'No RotaCloud iCal configured. Use the settings cog to add your link.', 'error');
      renderShiftCard(
        { status: currentStatus, window: currentWindow, location: currentLocation },
        null,
        'No active shift',
      );
      renderShiftCard(
        { status: nextStatus, window: nextWindow, location: nextLocation },
        null,
        'No upcoming shift',
      );
      return;
    }

    renderShiftCard(
      { status: currentStatus, window: currentWindow, location: currentLocation },
      payload.currentShift,
      'No active shift',
    );
    renderShiftCard(
      { status: nextStatus, window: nextWindow, location: nextLocation },
      payload.nextShift,
      'No upcoming shift',
    );
    setMessage(overviewMessage, 'Shift data loaded.', 'success');
  };

  const renderUpcomingShifts = (payload) => {
    const items = Array.isArray(payload && payload.shifts) ? payload.shifts : [];
    const weekDays = Array.isArray(payload && payload.weekDays) ? payload.weekDays : [];

    if (payload && payload.scope === 'week' && weekDays.length) {
      upcomingList.innerHTML = weekDays
        .map((day) => {
          const dayShifts = Array.isArray(day && day.shifts) ? day.shifts : [];
          const shiftsMarkup = dayShifts.length
            ? dayShifts.map((shift) => `
                <article class="overview-card upcoming-item">
                  <h3>${escapeHtml(shift.summary)}</h3>
                  <p class="overview-window">${escapeHtml(shift.windowLabel)}</p>
                  <p class="overview-location">${escapeHtml(shift.location || '')}</p>
                </article>
              `).join('')
            : '<p class="hours-empty">No shifts assigned.</p>';

          return `
            <section class="upcoming-day-group">
              <h3>${escapeHtml(day.dayLabel || day.dateIso || 'Day')}</h3>
              ${shiftsMarkup}
            </section>
          `;
        })
        .join('');
      return;
    }

    if (!items.length) {
      upcomingList.innerHTML = '<p class="hours-empty">No upcoming shifts in this period.</p>';
      return;
    }

    upcomingList.innerHTML = items
      .map((shift) => `
        <article class="overview-card upcoming-item">
          <h3>${escapeHtml(shift.summary)}</h3>
          <p class="overview-window">${escapeHtml(shift.windowLabel)}</p>
          <p class="overview-location">${escapeHtml(shift.location || '')}</p>
        </article>
      `)
      .join('');
  };

  const loadUpcomingShifts = async () => {
    if (!upcomingVisible) {
      return;
    }

    setMessage(upcomingMessage, 'Loading upcoming shifts...');
    const query = new URLSearchParams({
      scope: upcomingScope.value,
      offset: String(upcomingOffset),
      includeRestDays: upcomingIncludeRest.checked ? '1' : '0',
    });

    const response = await fetch(`${window.OCC_ASSIST.overviewUpcomingUrl}?${query.toString()}`, {
      cache: 'no-store',
    });
    const payload = await response.json();

    if (!response.ok || !payload.ok) {
      setMessage(upcomingMessage, payload.message || 'Unable to load upcoming shifts.', 'error');
      upcomingPeriod.textContent = '';
      upcomingList.innerHTML = '';
      return;
    }

    if (!payload.configured) {
      setMessage(upcomingMessage, 'No RotaCloud iCal configured. Use settings to add your link.', 'error');
      upcomingPeriod.textContent = '';
      upcomingList.innerHTML = '';
      return;
    }

    upcomingPeriod.textContent = `${payload.scope === 'week' ? 'Week' : 'Month'}: ${payload.periodLabel}`;
    setMessage(upcomingMessage, `Loaded ${payload.shifts.length} shift${payload.shifts.length === 1 ? '' : 's'}.`, 'success');
    renderUpcomingShifts(payload || {});
  };

  const setUpcomingVisibility = (visible) => {
    upcomingVisible = visible;
    upcomingControls.hidden = !visible;
    upcomingPeriod.hidden = !visible;
    upcomingMessage.hidden = !visible;
    upcomingList.hidden = !visible;
    toggleUpcomingButton.textContent = visible ? 'Hide Upcoming Shifts' : 'Show Upcoming Shifts';
    if (visible) {
      loadUpcomingShifts();
    }
  };

  refreshButton.addEventListener('click', () => {
    loadOverview();
  });

  toggleUpcomingButton.addEventListener('click', () => {
    setUpcomingVisibility(!upcomingVisible);
  });

  upcomingScope.addEventListener('change', () => {
    upcomingOffset = 0;
    loadUpcomingShifts();
  });

  upcomingIncludeRest.addEventListener('change', () => {
    loadUpcomingShifts();
  });

  upcomingPrev.addEventListener('click', () => {
    upcomingOffset -= 1;
    loadUpcomingShifts();
  });

  upcomingNext.addEventListener('click', () => {
    upcomingOffset += 1;
    loadUpcomingShifts();
  });

  upcomingRefresh.addEventListener('click', () => {
    loadUpcomingShifts();
  });

  setUpcomingVisibility(false);
  loadOverview();
}

function initializeSettingsPage() {
  const settingsForm = document.querySelector('#settings-form');
  const settingsMessage = document.querySelector('#settings-message');
  const icalInput = document.querySelector('#rotacloud-ical-url');

  if (!settingsForm || !settingsMessage || !icalInput) {
    return;
  }

  const loadSettings = async () => {
    setMessage(settingsMessage, 'Loading settings...');
    const response = await fetch(window.OCC_ASSIST.settingsRotacloudUrl, { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      setMessage(settingsMessage, payload.message || 'Unable to load settings.', 'error');
      return;
    }

    icalInput.value = payload.rotacloudIcalUrl || '';
    setMessage(settingsMessage, '');
  };

  settingsForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    setMessage(settingsMessage, 'Saving settings...');

    const response = await fetch(window.OCC_ASSIST.updateSettingsRotacloudUrl, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        rotacloudIcalUrl: icalInput.value.trim(),
      }),
    });

    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      setMessage(settingsMessage, payload.message || 'Unable to save settings.', 'error');
      return;
    }

    setMessage(settingsMessage, 'Settings saved.', 'success');
  });

  loadSettings();
}


function normalizeContactPhone(value) {
  return String(value || '').replace(/[^0-9]/g, '');
}

function sortContactsAlphabetically(contacts) {
  if (!Array.isArray(contacts)) return [];
  return [...contacts].sort((a, b) => {
    const aName = `${String(a.firstName || '').trim()} ${String(a.lastName || '').trim()}`.trim().toLowerCase();
    const bName = `${String(b.firstName || '').trim()} ${String(b.lastName || '').trim()}`.trim().toLowerCase();
    const byFullName = aName.localeCompare(bName, 'en', { sensitivity: 'base' });
    if (byFullName !== 0) return byFullName;

    const byFirstName = String(a.firstName || '').localeCompare(String(b.firstName || ''), 'en', { sensitivity: 'base' });
    if (byFirstName !== 0) return byFirstName;

    const byLastName = String(a.lastName || '').localeCompare(String(b.lastName || ''), 'en', { sensitivity: 'base' });
    if (byLastName !== 0) return byLastName;

    return Number(a.id || 0) - Number(b.id || 0);
  });
}

function renderContactsRolodex(container, contacts) {
  if (!container) return;
  if (!Array.isArray(contacts) || !contacts.length) {
    container.innerHTML = '<p class="saved-empty">No contacts found.</p>';
    return;
  }

  const sortedContacts = sortContactsAlphabetically(contacts);

  container.innerHTML = sortedContacts.map((contact) => {
    const fullName = `${contact.firstName || ''} ${contact.lastName || ''}`.trim() || 'Unknown';
    const importantBadge = contact.isImportant ? '<span class="contact-flag important">Important</span>' : '';
    const privateBadge = contact.isPrivate ? '<span class="contact-flag private">Internal only</span>' : '';
    return `
      <article class="contact-card">
        <header class="contact-card-head">
          <div>
            <h3>${escapeHtml(fullName)}</h3>
            <p>${escapeHtml(contact.jobRole || 'Unknown role')}${contact.jobTitle && contact.jobTitle !== contact.jobRole ? ` - ${escapeHtml(contact.jobTitle)}` : ''}</p>
          </div>
          <div class="contact-card-flags">${importantBadge}${privateBadge}</div>
        </header>
        <dl class="contact-detail-list">
          <div><dt>Depot / Location</dt><dd>${escapeHtml(contact.depotLocation || 'Unknown')}</dd></div>
          <div><dt>Phone</dt><dd><a href="tel:${escapeHtml(contact.phoneNumber || '')}">${escapeHtml(contact.phoneNumber || 'Unknown')}</a></dd></div>
        </dl>
      </article>
    `;
  }).join('');
}


function renderContactsAdminList(container, contacts) {
  if (!container) return;
  if (!Array.isArray(contacts) || !contacts.length) {
    container.innerHTML = '<p class="saved-empty">No contacts found.</p>';
    return;
  }

  const sortedContacts = sortContactsAlphabetically(contacts);

  container.innerHTML = sortedContacts.map((contact) => {
    const fullName = `${contact.firstName || ''} ${contact.lastName || ''}`.trim() || 'Unknown';
    const importantBadge = contact.isImportant ? '<span class="contact-flag important">Important</span>' : '';
    const privateBadge = contact.isPrivate ? '<span class="contact-flag private">Internal only</span>' : '';
    return `
      <article class="contact-card" data-contact-id="${contact.id}">
        <header class="contact-card-head">
          <div>
            <h3>${escapeHtml(fullName)}</h3>
            <p>${escapeHtml(contact.jobRole || 'Unknown role')}${contact.jobTitle && contact.jobTitle !== contact.jobRole ? ` - ${escapeHtml(contact.jobTitle)}` : ''}</p>
          </div>
          <div class="contact-card-flags">${importantBadge}${privateBadge}</div>
        </header>
        <dl class="contact-detail-list">
          <div><dt>Depot / Location</dt><dd>${escapeHtml(contact.depotLocation || 'Unknown')}</dd></div>
          <div><dt>Phone</dt><dd><a href="tel:${escapeHtml(contact.phoneNumber || '')}">${escapeHtml(contact.phoneNumber || 'Unknown')}</a></dd></div>
        </dl>
        <div class="contacts-card-actions">
          <button type="button" class="btn secondary compact" data-action="edit-contact" data-contact-id="${contact.id}">Edit</button>\n          <button type="button" class="btn danger compact" data-action="delete-contact" data-contact-id="${contact.id}">Delete</button>
        </div>
      </article>
    `;
  }).join('');
}

function initializeContactsPage() {
  const contactsApp = document.querySelector('#contacts-app');
  const contactsList = document.querySelector('#contacts-list');
  const contactsSearchInput = document.querySelector('#contacts-search-input');
  const contactsSearchMeta = document.querySelector('#contacts-search-meta');
  const contactsMessage = document.querySelector('#contacts-message');

  if (!contactsApp || !contactsList || !contactsSearchInput) {
    return;
  }

  let allContacts = [];

  const applySearch = () => {
    const query = String(contactsSearchInput.value || '').trim().toLowerCase();
    if (!query) {
      renderContactsRolodex(contactsList, allContacts);
      if (contactsSearchMeta) contactsSearchMeta.textContent = `${allContacts.length} contact${allContacts.length === 1 ? '' : 's'} available.`;
      return;
    }

    const queryDigits = normalizeContactPhone(query);
    const filtered = allContacts.filter((contact) => {
      const fullName = `${contact.firstName || ''} ${contact.lastName || ''}`.toLowerCase();
      const jobRole = String(contact.jobRole || '').toLowerCase();
      const jobTitle = String(contact.jobTitle || '').toLowerCase();
      const location = String(contact.depotLocation || '').toLowerCase();
      const phone = normalizeContactPhone(contact.phoneNumber || '');
      const textMatch = fullName.includes(query) || jobRole.includes(query) || jobTitle.includes(query) || location.includes(query);
      const phoneMatch = Boolean(queryDigits) && phone.includes(queryDigits);
      return textMatch || phoneMatch;
    });

    renderContactsRolodex(contactsList, filtered);
    if (contactsSearchMeta) contactsSearchMeta.textContent = `${filtered.length} match${filtered.length === 1 ? '' : 'es'} for "${query}".`;
  };

  const loadContacts = async () => {
    if (!window.OCC_ASSIST.contactsApiUrl) {
      setMessage(contactsMessage, 'Contacts API is not configured.', 'error');
      return;
    }
    setMessage(contactsMessage, 'Loading contacts...');
    const response = await fetch(window.OCC_ASSIST.contactsApiUrl, { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      setMessage(contactsMessage, payload.message || 'Unable to load contacts.', 'error');
      return;
    }

    allContacts = sortContactsAlphabetically(Array.isArray(payload.contacts) ? payload.contacts : []);
    renderContactsRolodex(contactsList, allContacts);
    if (contactsSearchMeta) contactsSearchMeta.textContent = `${allContacts.length} contact${allContacts.length === 1 ? '' : 's'} available.`;
    setMessage(contactsMessage, 'Contacts loaded.', 'success');
  };

  contactsSearchInput.addEventListener('input', applySearch);
  loadContacts().catch((error) => {
    setMessage(contactsMessage, error.message || 'Unable to load contacts.', 'error');
  });
}

function initializeUsersPage() {
  const userForm = document.querySelector('#user-form');
  const usersList = document.querySelector('#users-list');
  const refreshButton = document.querySelector('#refresh-users');
  const usersMessage = document.querySelector('#users-message');
  const formMessage = document.querySelector('#user-form-message');
  const gtfsUploadForm = document.querySelector('#gtfs-upload-form');
  const gtfsFileInput = document.querySelector('#gtfs-file');
  const gtfsUploadMessage = document.querySelector('#gtfs-upload-message');
  const gtfsUploadSummary = document.querySelector('#gtfs-upload-summary');
  const refreshGtfsStatusButton = document.querySelector('#refresh-gtfs-status');
    const toggleGtfsManualLockButton = document.querySelector('#toggle-gtfs-manual-lock');
  const refreshAdminDataStatusButton = document.querySelector('#refresh-admin-data-status');
  const adminDataLastCheck = document.querySelector('#admin-data-last-check');
  const adminBodsStatus = document.querySelector('#admin-bods-status');
  const adminGtfsStatus = document.querySelector('#admin-gtfs-status');
    const gtfsManualLockSummary = document.querySelector('#gtfs-manual-lock-summary');
  const adminDataStatusMessage = document.querySelector('#admin-data-status-message');
  const contactsEncryptionSummary = document.querySelector('#contacts-encryption-summary');
  const contactsEncryptionMessage = document.querySelector('#contacts-encryption-message');
  const refreshContactsEncryptionStatusButton = document.querySelector('#refresh-contacts-encryption-status');
  const contactForm = document.querySelector('#contact-form');
  const contactFormMessage = document.querySelector('#contact-form-message');
  const contactsAdminMessage = document.querySelector('#contacts-admin-message');
  const contactsAdminList = document.querySelector('#contacts-admin-list');
  const contactsAdminSearchQuery = document.querySelector('#contacts-admin-search-query');
  const contactsAdminSearchSubmit = document.querySelector('#contacts-admin-search-submit');
  const contactsAdminSearchClear = document.querySelector('#contacts-admin-search-clear');
  const contactEditIdInput = document.querySelector('#contact-edit-id');
  const saveContactButton = document.querySelector('#save-contact-button');
  const cancelContactEditButton = document.querySelector('#cancel-contact-edit');

  if (!userForm || !usersList) {
    return;
  }

  const createPermissionInputs = Array.from(userForm.querySelectorAll('input[name="permission"]'));
  const createAdminPermissionInput = createPermissionInputs.find((input) => input.value === 'admin_privileges');
  const syncCreateUserPermissionInputs = () => {
    if (!createAdminPermissionInput || !createAdminPermissionInput.checked) {
      createPermissionInputs.forEach((input) => {
        if (input.value !== 'admin_privileges') {
          input.disabled = false;
        }
      });
      return;
    }

    createPermissionInputs.forEach((input) => {
      if (input.value !== 'admin_privileges') {
        input.checked = true;
        input.disabled = true;
      }
    });
  };

  if (createAdminPermissionInput) {
    createAdminPermissionInput.addEventListener('change', syncCreateUserPermissionInputs);
    syncCreateUserPermissionInputs();
  }

  let contactsAdminCache = [];

  const resetContactEditMode = () => {
    if (contactEditIdInput) contactEditIdInput.value = '';
    if (saveContactButton) saveContactButton.textContent = 'Add contact';
    if (cancelContactEditButton) cancelContactEditButton.hidden = true;
  };

  const startContactEditMode = (contactId) => {
    const idValue = String(contactId || '').trim();
    if (!idValue) return;
    const match = contactsAdminCache.find((contact) => String(contact.id) === idValue);
    if (!match || !contactForm) return;

    contactForm.firstName.value = match.firstName || '';
    contactForm.lastName.value = match.lastName || '';
    contactForm.jobRole.value = match.jobRole || '';
    contactForm.depotLocation.value = match.depotLocation || '';
    contactForm.phoneNumber.value = match.phoneNumber || '';
    contactForm.isImportant.checked = Boolean(match.isImportant);
    contactForm.isPrivate.checked = Boolean(match.isPrivate);
    if (contactEditIdInput) contactEditIdInput.value = String(match.id);
    if (saveContactButton) saveContactButton.textContent = 'Save contact changes';
    if (cancelContactEditButton) cancelContactEditButton.hidden = false;
    contactForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const loadUsers = async () => {
    setMessage(usersMessage, 'Loading users...');
    const response = await fetch(window.OCC_ASSIST.usersApiUrl);
    const payload = await response.json();

    if (!response.ok) {
      setMessage(usersMessage, payload.message || 'Unable to load users.', 'error');
      return;
    }

    renderUsers(usersList, payload.users, payload.permissionLabels);
    setMessage(usersMessage, `${payload.users.length} user${payload.users.length === 1 ? '' : 's'} loaded.`, 'success');
  };


  const loadContactsAdmin = async (queryOverride = null) => {
    if (!contactsAdminList || !window.OCC_ASSIST.contactsApiUrl) {
      return;
    }

    const rawQuery = queryOverride === null
      ? String(contactsAdminSearchQuery ? contactsAdminSearchQuery.value : '').trim()
      : String(queryOverride || '').trim();

    if (!rawQuery) {
      contactsAdminCache = [];
      contactsAdminList.innerHTML = '<p class="saved-empty">Search for a contact to view, edit, or delete.</p>';
      setMessage(contactsAdminMessage, 'Enter a search term to find contacts.');
      return;
    }

    setMessage(contactsAdminMessage, `Searching contacts for "${rawQuery}"...`);
    const response = await fetch(`${window.OCC_ASSIST.contactsApiUrl}?q=${encodeURIComponent(rawQuery)}`, { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      setMessage(contactsAdminMessage, payload.message || 'Unable to search contacts.', 'error');
      return;
    }

    const contacts = sortContactsAlphabetically(Array.isArray(payload.contacts) ? payload.contacts : []);
    contactsAdminCache = contacts;
    renderContactsAdminList(contactsAdminList, contacts);
    setMessage(contactsAdminMessage, `${contacts.length} match${contacts.length === 1 ? '' : 'es'} for "${rawQuery}".`, 'success');
  };

  const setGtfsManualLockButton = (enabled) => {
      if (!toggleGtfsManualLockButton) {
        return;
      }
      const isEnabled = Boolean(enabled);
      toggleGtfsManualLockButton.dataset.enabled = isEnabled ? '1' : '0';
      toggleGtfsManualLockButton.textContent = isEnabled ? 'Manual upload lock: ON' : 'Manual upload lock: OFF';
    };

    const loadGtfsStatus = async () => {
    if (!window.OCC_ASSIST.gtfsStatusUrl || !gtfsUploadSummary) {
      return;
    }

    const response = await fetch(window.OCC_ASSIST.gtfsStatusUrl, { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || 'Unable to load GTFS status.');
    }

    if (!payload.configured) {
      gtfsUploadSummary.textContent = payload.message || 'No GTFS ZIP uploaded yet.';
      return;
    }

    const uploadedAt = payload.uploadedAt ? new Date(payload.uploadedAt).toLocaleString() : 'Unknown';
    const filename = payload.originalFilename || 'Uploaded file';
    const xmlCount = Number(payload.xmlSourceFileCount || 0);
      const routeRows = Number(payload.sourceRouteRowCount || 0);
      const sourceDetails = xmlCount > 0 ? ` Source XML files: ${xmlCount}. Source route rows: ${routeRows}.` : '';
      gtfsUploadSummary.textContent = `${payload.routeCount} routes available from ${filename} (uploaded ${uploadedAt}).${sourceDetails}`;
      if (Object.prototype.hasOwnProperty.call(payload, 'manualLockEnabled')) {
        const lockEnabled = Boolean(payload.manualLockEnabled);
        setGtfsManualLockButton(lockEnabled);
        if (gtfsManualLockSummary) {
          gtfsManualLockSummary.textContent = lockEnabled
            ? 'Manual upload lock is ON. Auto-download updates are paused.'
            : 'Manual upload lock is OFF. Auto-download updates are allowed.';
        }
      }
  };


  const loadContactsEncryptionStatus = async () => {
    if (!window.OCC_ASSIST.contactsEncryptionStatusUrl || !contactsEncryptionSummary) {
      return;
    }

    const response = await fetch(window.OCC_ASSIST.contactsEncryptionStatusUrl, { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok || !payload.ok || !payload.status) {
      throw new Error(payload.message || 'Unable to load contacts encryption status.');
    }

    const status = payload.status || {};
    const checkedAt = status.checkedAt ? new Date(status.checkedAt).toLocaleString() : 'Unknown';
    contactsEncryptionSummary.textContent = `Contacts: ${Number(status.totalContacts || 0)}. Fully encrypted: ${Number(status.fullyEncryptedContacts || 0)}. Partial: ${Number(status.partiallyEncryptedContacts || 0)}. Plaintext: ${Number(status.plaintextContacts || 0)}. Coverage: ${Number(status.encryptedPercentage || 0)}%. Checked: ${checkedAt}.`;
    if (contactsEncryptionMessage) {
      setMessage(
        contactsEncryptionMessage,
        status.allEncrypted ? 'All contacts are encrypted at rest.' : 'Some contacts are not fully encrypted. Review migration and key configuration.',
        status.allEncrypted ? 'success' : 'error',
      );
    }
  };

  const loadAdminDataStatus = async (force = false) => {
    if (!window.OCC_ASSIST.adminDataStatusUrl) {
      return;
    }

    const suffix = force ? '?force=1' : '';
    const response = await fetch(`${window.OCC_ASSIST.adminDataStatusUrl}${suffix}`, { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || 'Unable to load admin data status.');
    }

    const status = payload.status || {};
    const bods = status.bods || {};
    const gtfs = status.gtfs || {};
    const checkedAt = status.lastCheckAt ? new Date(status.lastCheckAt).toLocaleString() : 'Unknown';

    if (adminDataLastCheck) {
      adminDataLastCheck.textContent = `Last automatic check: ${checkedAt}.`;
    }
    if (adminBodsStatus) {
      const bodsState = bods.ok ? 'OK' : 'Issue';
      const bodsActive = bods.active ? 'active' : 'inactive';
      const sourceTs = bods.sourceTimestamp ? ` Source timestamp: ${bods.sourceTimestamp}.` : '';
      adminBodsStatus.textContent = `BODS: ${bodsState}, ${bodsActive}. Vehicles: ${Number(bods.vehicleCount || 0)}. ${bods.message || ''}${sourceTs}`.trim();
      adminBodsStatus.className = `message ${bods.ok ? 'success' : 'error'}`;
    }
    if (adminGtfsStatus) {
      const gtfsState = gtfs.ok ? 'OK' : 'Issue';
      const gtfsActive = gtfs.active ? 'active' : 'inactive';
      const uploadedAt = gtfs.uploadedAt ? new Date(gtfs.uploadedAt).toLocaleString() : 'Unknown';
      const filename = gtfs.originalFilename || 'No file';
      const autoNote = gtfs.autoUpdateMessage ? ` ${gtfs.autoUpdateMessage}` : '';
        const xmlCount = Number(gtfs.xmlSourceFileCount || 0);
        const sourceRouteRows = Number(gtfs.sourceRouteRowCount || 0);
        const sourceDetails = xmlCount > 0 ? ` Source XML files: ${xmlCount}. Source route rows: ${sourceRouteRows}.` : '';
        const lockEnabled = Boolean(gtfs.manualLockEnabled);
        setGtfsManualLockButton(lockEnabled);
        if (gtfsManualLockSummary) {
          gtfsManualLockSummary.textContent = lockEnabled
            ? 'Manual upload lock is ON. Auto-download updates are paused.'
            : 'Manual upload lock is OFF. Auto-download updates are allowed.';
        }
      adminGtfsStatus.textContent = `GTFS: ${gtfsState}, ${gtfsActive}. Routes: ${Number(gtfs.routeCount || 0)}. File: ${filename}. Uploaded: ${uploadedAt}.${sourceDetails} ${gtfs.message || ''}${autoNote}`.trim();
      adminGtfsStatus.className = `message ${gtfs.ok ? 'success' : 'error'}`;
    }
    if (adminDataStatusMessage) {
      setMessage(adminDataStatusMessage, force ? 'Admin data check completed.' : 'Admin data status loaded.', 'success');
    }
  };

  userForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const email = userForm.email.value.trim();
    const password = userForm.password.value;
    const permissions = {};
    userForm.querySelectorAll('input[name="permission"]').forEach((input) => {
      permissions[input.value] = input.checked;
    });

    const response = await fetch(window.OCC_ASSIST.createUserUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ email, password, permissions }),
    });
    const payload = await response.json();

    if (!response.ok) {
      setMessage(formMessage, payload.message || 'Unable to create user.', 'error');
      return;
    }

    userForm.reset();
    setMessage(formMessage, 'User created successfully.', 'success');
    await loadUsers();
  });

  usersList.addEventListener('change', async (event) => {
    const toggle = event.target;
    if (!toggle.matches('[data-permission-key]')) {
      return;
    }

    const userId = toggle.dataset.userId;
    const permissionKey = toggle.dataset.permissionKey;
    const enabled = toggle.checked;

    const response = await fetch(`${window.OCC_ASSIST.permissionsBaseUrl}/${userId}/permissions`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ permissionKey, enabled }),
    });
    const payload = await response.json();

    if (!response.ok) {
      toggle.checked = !enabled;
      setMessage(usersMessage, payload.message || 'Unable to update permission.', 'error');
      return;
    }

    setMessage(usersMessage, 'Permission updated.', 'success');
    await loadUsers();
  });

  usersList.addEventListener('click', async (event) => {
    const deleteButton = event.target.closest('[data-action="delete-user"]');
    if (deleteButton) {
      event.preventDefault();
      const userId = deleteButton.dataset.userId;
      const userEmail = deleteButton.dataset.userEmail || 'this user';
      const isConfirmed = window.confirm(`Delete ${userEmail} and remove all of their saved data?`);

      if (!isConfirmed) {
        return;
      }

      setMessage(usersMessage, 'Deleting user and saved data...');
      const response = await fetch(`${window.OCC_ASSIST.permissionsBaseUrl}/${userId}`, {
        method: 'DELETE',
      });
      const payload = await response.json();

      if (!response.ok) {
        setMessage(usersMessage, payload.message || 'Unable to delete user.', 'error');
        return;
      }

      setMessage(usersMessage, 'User deleted and saved data removed.', 'success');
      await loadUsers();
      return;
    }

    const forceLogoutButton = event.target.closest('[data-action="force-logout"]');
    if (forceLogoutButton) {
      event.preventDefault();
      const userId = forceLogoutButton.dataset.userId;
      const userEmail = forceLogoutButton.dataset.userEmail || 'this user';
      const isConfirmed = window.confirm(`End the current active session for ${userEmail}?`);

      if (!isConfirmed) {
        return;
      }

      setMessage(usersMessage, 'Ending active session...');
      const response = await fetch(`${window.OCC_ASSIST.permissionsBaseUrl}/${userId}/sessions/force-logout`, {
        method: 'POST',
      });
      const payload = await response.json();

      if (!response.ok) {
        setMessage(usersMessage, payload.message || 'Unable to end the active session.', 'error');
        return;
      }

      setMessage(usersMessage, 'Active session ended.', 'success');
      await loadUsers();
      return;
    }

    const forcePasswordResetButton = event.target.closest('[data-action="force-password-reset"]');
    if (!forcePasswordResetButton) {
      return;
    }

    event.preventDefault();
    const userId = forcePasswordResetButton.dataset.userId;
    const userEmail = forcePasswordResetButton.dataset.userEmail || 'this user';
    const isConfirmed = window.confirm(`Require ${userEmail} to reset their password at next sign-in?`);

    if (!isConfirmed) {
      return;
    }

    setMessage(usersMessage, 'Requesting password reset...');
    const response = await fetch(`${window.OCC_ASSIST.permissionsBaseUrl}/${userId}/password-reset`, {
      method: 'POST',
    });
    const payload = await response.json();

    if (!response.ok) {
      setMessage(usersMessage, payload.message || 'Unable to request password reset.', 'error');
      return;
    }

    setMessage(usersMessage, 'Password reset requested.', 'success');
    await loadUsers();
  });

    refreshButton.addEventListener('click', () => {
      loadUsers();
    });

    if (refreshAdminDataStatusButton) {
      refreshAdminDataStatusButton.addEventListener('click', () => {
        if (adminDataStatusMessage) {
          setMessage(adminDataStatusMessage, 'Running admin data check...');
        }
        loadAdminDataStatus(true).catch((error) => {
          if (adminDataStatusMessage) {
            setMessage(adminDataStatusMessage, error.message || 'Unable to run admin data check.', 'error');
          }
        });
      });
    }

    if (adminDataLastCheck || adminBodsStatus || adminGtfsStatus || adminDataStatusMessage) {
      loadAdminDataStatus(false).catch((error) => {
        if (adminDataStatusMessage) {
          setMessage(adminDataStatusMessage, error.message || 'Unable to load admin data status.', 'error');
        }
      });
    }

    if (refreshContactsEncryptionStatusButton) {
      refreshContactsEncryptionStatusButton.addEventListener('click', () => {
        if (contactsEncryptionMessage) {
          setMessage(contactsEncryptionMessage, 'Refreshing contacts encryption status...');
        }
        loadContactsEncryptionStatus().catch((error) => {
          if (contactsEncryptionMessage) {
            setMessage(contactsEncryptionMessage, error.message || 'Unable to load contacts encryption status.', 'error');
          }
        });
      });
    }

    if (contactsEncryptionSummary || contactsEncryptionMessage) {
      loadContactsEncryptionStatus().catch((error) => {
        if (contactsEncryptionMessage) {
          setMessage(contactsEncryptionMessage, error.message || 'Unable to load contacts encryption status.', 'error');
        }
      });
    }


  if (contactForm && contactFormMessage && contactsAdminMessage) {
    contactForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!window.OCC_ASSIST.createContactUrl) {
        setMessage(contactFormMessage, 'Contacts API is not configured.', 'error');
        return;
      }

      const editingId = contactEditIdInput ? String(contactEditIdInput.value || '').trim() : '';
      const payload = {
        firstName: contactForm.firstName.value.trim(),
        lastName: contactForm.lastName.value.trim(),
        jobRole: contactForm.jobRole.value.trim(),
        jobTitle: contactForm.jobTitle ? contactForm.jobTitle.value.trim() : '',
        depotLocation: contactForm.depotLocation.value.trim(),
        phoneNumber: contactForm.phoneNumber.value.trim(),
        isImportant: Boolean(contactForm.isImportant.checked),
        isPrivate: Boolean(contactForm.isPrivate.checked),
      };

      const isEditing = Boolean(editingId);
      const endpoint = isEditing ? `${window.OCC_ASSIST.contactsApiUrl}/${encodeURIComponent(editingId)}` : window.OCC_ASSIST.createContactUrl;
      const method = isEditing ? 'PATCH' : 'POST';
      setMessage(contactFormMessage, isEditing ? 'Saving contact changes...' : 'Adding contact...');

      const response = await fetch(endpoint, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        if (!isEditing && response.status === 409 && result.duplicate) {
          const confirmSave = window.confirm(result.message || 'Possible duplicate detected. Save anyway?');
          if (!confirmSave) {
            setMessage(contactFormMessage, 'Duplicate save cancelled.', 'error');
            return;
          }

          const duplicatePayload = { ...payload, forceSaveDuplicate: true };
          setMessage(contactFormMessage, 'Saving duplicate contact...');
          const duplicateResponse = await fetch(window.OCC_ASSIST.createContactUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(duplicatePayload),
          });
          const duplicateResult = await duplicateResponse.json();
          if (!duplicateResponse.ok || !duplicateResult.ok) {
            setMessage(contactFormMessage, duplicateResult.message || 'Unable to save duplicate contact.', 'error');
            return;
          }
        } else {
          setMessage(contactFormMessage, result.message || 'Unable to save contact.', 'error');
          return;
        }
      }

      contactForm.reset();
      resetContactEditMode();
      setMessage(contactFormMessage, isEditing ? 'Contact updated.' : 'Contact added.', 'success');
      await loadContactsAdmin();
    });

    if (contactsAdminSearchSubmit) {
      contactsAdminSearchSubmit.addEventListener('click', () => {
        loadContactsAdmin().catch((error) => {
          setMessage(contactsAdminMessage, error.message || 'Unable to search contacts.', 'error');
        });
      });
    }

    if (contactsAdminSearchQuery) {
      contactsAdminSearchQuery.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          loadContactsAdmin().catch((error) => {
            setMessage(contactsAdminMessage, error.message || 'Unable to search contacts.', 'error');
          });
        }
      });
    }

    if (contactsAdminSearchClear) {
      contactsAdminSearchClear.addEventListener('click', () => {
        if (contactsAdminSearchQuery) contactsAdminSearchQuery.value = '';
        contactsAdminCache = [];
        if (contactsAdminList) {
          contactsAdminList.innerHTML = '<p class="saved-empty">Search for a contact to view, edit, or delete.</p>';
        }
        setMessage(contactsAdminMessage, 'Search cleared.');
      });
    }

    if (cancelContactEditButton) {
      cancelContactEditButton.addEventListener('click', () => {
        contactForm.reset();
        resetContactEditMode();
        setMessage(contactFormMessage, 'Edit cancelled.', 'success');
      });
    }

    if (contactsAdminList) {
      contactsAdminList.addEventListener('click', async (event) => {
        const editButton = event.target.closest('[data-action="edit-contact"]');
        if (editButton) {
          event.preventDefault();
          const contactId = editButton.dataset.contactId;
          startContactEditMode(contactId);
          setMessage(contactFormMessage, 'Editing existing contact.', 'success');
          return;
        }

        const deleteButton = event.target.closest('[data-action="delete-contact"]');
        if (!deleteButton) return;

        event.preventDefault();
        const contactId = String(deleteButton.dataset.contactId || '').trim();
        if (!contactId) return;

        const match = contactsAdminCache.find((contact) => String(contact.id) === contactId);
        const fullName = match ? `${match.firstName || ''} ${match.lastName || ''}`.trim() : 'this contact';
        const confirmed = window.confirm(`Delete ${fullName}? This cannot be undone.`);
        if (!confirmed) return;

        setMessage(contactsAdminMessage, `Deleting ${fullName}...`);
        const response = await fetch(`${window.OCC_ASSIST.contactsApiUrl}/${encodeURIComponent(contactId)}`, {
          method: 'DELETE',
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          setMessage(contactsAdminMessage, payload.message || 'Unable to delete contact.', 'error');
          return;
        }

        if (contactEditIdInput && contactEditIdInput.value === contactId) {
          contactForm.reset();
          resetContactEditMode();
        }

        setMessage(contactsAdminMessage, 'Contact deleted.', 'success');
        await loadContactsAdmin();
      });
    }

    if (contactsAdminList) {
      contactsAdminList.innerHTML = '<p class="saved-empty">Search for a contact to view, edit, or delete.</p>';
    }
  }

  if (gtfsUploadForm && gtfsFileInput && gtfsUploadMessage && gtfsUploadSummary) {
    gtfsUploadForm.addEventListener('submit', async (event) => {
      event.preventDefault();

      const file = gtfsFileInput.files && gtfsFileInput.files[0] ? gtfsFileInput.files[0] : null;
      if (!file) {
        setMessage(gtfsUploadMessage, 'Choose a GTFS ZIP file before uploading.', 'error');
        return;
      }

      const formData = new FormData();
      formData.append('gtfsZipFile', file);
      setMessage(gtfsUploadMessage, 'Uploading GTFS ZIP and extracting route paths...');

      const response = await fetch(window.OCC_ASSIST.gtfsUploadUrl, {
        method: 'POST',
        body: formData,
      });
      const payload = await response.json();

      if (!response.ok || !payload.ok) {
        setMessage(gtfsUploadMessage, payload.message || 'Unable to upload GTFS ZIP file.', 'error');
        return;
      }

      setMessage(gtfsUploadMessage, `Upload complete. ${payload.routeCount} routes extracted.`, 'success');
      gtfsUploadForm.reset();
      await loadGtfsStatus();
    });

    if (refreshGtfsStatusButton) {
      refreshGtfsStatusButton.addEventListener('click', () => {
        loadGtfsStatus().catch((error) => {
          setMessage(gtfsUploadMessage, error.message || 'Unable to refresh GTFS status.', 'error');
        });
      });
    }

      if (toggleGtfsManualLockButton && window.OCC_ASSIST.gtfsManualLockUrl) {
        toggleGtfsManualLockButton.addEventListener('click', async () => {
          const current = toggleGtfsManualLockButton.dataset.enabled === '1';
          const next = !current;
          if (adminDataStatusMessage) {
            setMessage(adminDataStatusMessage, next ? 'Enabling manual upload lock...' : 'Disabling manual upload lock...');
          }

          const response = await fetch(window.OCC_ASSIST.gtfsManualLockUrl, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ enabled: next }),
          });
          const payload = await response.json();
          if (!response.ok || !payload.ok) {
            throw new Error(payload.message || 'Unable to update manual lock state.');
          }

          setGtfsManualLockButton(Boolean(payload.enabled));
          if (adminDataStatusMessage) {
            setMessage(adminDataStatusMessage, payload.message || 'Manual lock updated.', 'success');
          }

          await loadAdminDataStatus(false);
          await loadGtfsStatus();
        });
      }
  }

  loadUsers();
  if (contactsAdminList) {
    loadContactsAdmin().catch((error) => {
      if (contactsAdminMessage) {
        setMessage(contactsAdminMessage, error.message || 'Unable to load contacts.', 'error');
      }
    });
  }
  if (gtfsUploadSummary) {
    loadGtfsStatus().catch((error) => {
      if (gtfsUploadMessage) {
        setMessage(gtfsUploadMessage, error.message || 'Unable to load GTFS status.', 'error');
      }
    });
  }
}

function renderUsers(container, users, permissionLabels) {
  const currentUser = window.OCC_ASSIST.currentUser || {};
  const canDeleteUsers = Boolean(
    currentUser.isSuperadmin || currentUser.permissions?.admin_privileges,
  );

  container.innerHTML = users
    .map((user) => {
      const isSiteAdmin = Boolean(user.isSuperadmin || user.permissions.admin_privileges);
      const permissionMarkup = Object.entries(permissionLabels)
        .map(([permissionKey, label]) => {
          const isAdminPrivilegeToggle = permissionKey === 'admin_privileges';
          const isLocked = Boolean(user.isSuperadmin || (isSiteAdmin && !isAdminPrivilegeToggle));
          const isEnabled = Boolean(user.permissions[permissionKey] || (isSiteAdmin && !isAdminPrivilegeToggle));
          return `
            <label class="permission-toggle">
              <span>${label}</span>
              <span class="toggle">
                <input
                  type="checkbox"
                  data-user-id="${user.id}"
                  data-permission-key="${permissionKey}"
                  ${isEnabled ? 'checked' : ''}
                  ${isLocked ? 'disabled' : ''}
                />
                <span></span>
              </span>
            </label>
          `;
        })
        .join('');

      const isSelf = Number(currentUser.id) === Number(user.id);
      const session = user.session || {};
      const sessionDurationLabel = session.sessionDurationSeconds != null
        ? `${Math.max(0, Math.floor(Number(session.sessionDurationSeconds) / 60))} min`
        : '0 min';
      const sessionStatusLabel = session.isActive ? 'Active now' : 'No active session';
      const sessionSummary = session.hasSession
        ? `${sessionStatusLabel} • Signed in for ${sessionDurationLabel}`
        : 'No active session';
      const deleteButtonMarkup = canDeleteUsers && !isSelf
        ? `<button class="btn danger compact" type="button" data-action="delete-user" data-user-id="${user.id}" data-user-email="${user.email}">Delete</button>`
        : '';
      const forceLogoutButtonMarkup = canDeleteUsers && !isSelf
        ? `<button class="btn secondary compact" type="button" data-action="force-logout" data-user-id="${user.id}" data-user-email="${user.email}">Force logout</button>`
        : '';
      const forcePasswordResetButtonMarkup = canDeleteUsers && !isSelf
        ? `<button class="btn secondary compact" type="button" data-action="force-password-reset" data-user-id="${user.id}" data-user-email="${user.email}">Force password reset</button>`
        : '';
      const roleBadgeClass = user.isSuperadmin
        ? 'badge-superadmin'
        : (user.permissions.admin_privileges ? 'badge-site-admin' : 'badge-standard-user');
      const roleBadgeLabel = user.isSuperadmin
        ? 'Superadmin'
        : (user.permissions.admin_privileges ? 'Site admin' : 'Standard user');

      return `
        <article class="user-card">
          <div class="user-card-head">
            <div>
              <h3>${user.email}</h3>
              <p class="user-meta">Created ${user.createdAt}</p>
              <p class="user-meta">${sessionSummary}</p>
            </div>
            <div class="user-card-actions">
              <span class="badge ${roleBadgeClass}">${roleBadgeLabel}</span>
              ${deleteButtonMarkup}
              ${forceLogoutButtonMarkup}
              ${forcePasswordResetButtonMarkup}
            </div>
          </div>
          <div class="permission-list">${permissionMarkup}</div>
        </article>
      `;
    })
    .join('');
}
