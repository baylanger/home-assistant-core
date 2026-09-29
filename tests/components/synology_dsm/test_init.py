"""Tests for the Synology DSM component."""

import ssl
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientConnectorCertificateError
from aiohttp.client_reqrep import ConnectionKey
from synology_dsm.exceptions import (
    SynologyDSMLoginInvalidException,
    SynologyDSMNotLoggedInException,
    SynologyDSMSSLException,
)

from homeassistant.components.synology_dsm.const import (
    CONF_BACKUP_PATH,
    CONF_BACKUP_SHARE,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    SERVICES,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from .consts import HOST, MACS, PASSWORD, PORT, USE_SSL, USERNAME

from tests.common import MockConfigEntry


async def test_services_registered(hass: HomeAssistant, mock_dsm: MagicMock) -> None:
    """Test if all services are registered."""
    with (
        patch(
            "homeassistant.components.synology_dsm.common.SynologyDSM",
            return_value=mock_dsm,
        ),
        patch("homeassistant.components.synology_dsm.PLATFORMS", return_value=[]),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: HOST,
                CONF_PORT: PORT,
                CONF_SSL: USE_SSL,
                CONF_USERNAME: USERNAME,
                CONF_PASSWORD: PASSWORD,
                CONF_MAC: MACS[0],
            },
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        for service in SERVICES:
            assert hass.services.has_service(DOMAIN, service)


async def test_reauth_triggered(hass: HomeAssistant) -> None:
    """Test if reauthentication flow is triggered."""
    with patch(
        "homeassistant.components.synology_dsm.SynoApi.async_setup",
        side_effect=SynologyDSMLoginInvalidException(USERNAME),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: HOST,
                CONF_PORT: PORT,
                CONF_SSL: USE_SSL,
                CONF_USERNAME: USERNAME,
                CONF_PASSWORD: PASSWORD,
                CONF_MAC: MACS[0],
            },
        )
        entry.add_to_hass(hass)
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_ERROR
        assert any(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))


async def test_config_entry_migrations(
    hass: HomeAssistant, mock_dsm: MagicMock
) -> None:
    """Test if reauthentication flow is triggered."""
    with (
        patch(
            "homeassistant.components.synology_dsm.common.SynologyDSM",
            return_value=mock_dsm,
        ),
        patch("homeassistant.components.synology_dsm.PLATFORMS", return_value=[]),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: HOST,
                CONF_PORT: PORT,
                CONF_SSL: USE_SSL,
                CONF_USERNAME: USERNAME,
                CONF_PASSWORD: PASSWORD,
                CONF_MAC: MACS[0],
            },
            options={CONF_SCAN_INTERVAL: 30},
        )
        entry.add_to_hass(hass)

        assert CONF_VERIFY_SSL not in entry.data
        assert CONF_BACKUP_SHARE not in entry.options
        assert CONF_BACKUP_PATH not in entry.options

        assert await hass.config_entries.async_setup(entry.entry_id)

        assert entry.data[CONF_VERIFY_SSL] == DEFAULT_VERIFY_SSL
        assert CONF_SCAN_INTERVAL not in entry.options
        assert entry.options[CONF_BACKUP_SHARE] is None
        assert entry.options[CONF_BACKUP_PATH] is None


def _cert_expired_error() -> ClientConnectorCertificateError:
    """Return the error aiohttp raises for an expired certificate."""
    return ClientConnectorCertificateError(
        ConnectionKey(HOST, PORT, True, True, None, None, None),
        ssl.SSLCertVerificationError(
            1,
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "certificate has expired (_ssl.c:1032)",
        ),
    )


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_SSL: USE_SSL,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_MAC: MACS[0],
            CONF_VERIFY_SSL: True,
        },
    )


async def test_setup_expired_certificate(hass: HomeAssistant) -> None:
    """Test an expired certificate is reported as such, not as an auth problem.

    Uses the real library: only the aiohttp session is faked.
    """
    session = MagicMock()
    session.get = AsyncMock(side_effect=_cert_expired_error())
    entry = _entry()
    entry.add_to_hass(hass)

    with patch(
        "homeassistant.components.synology_dsm.common.async_get_clientsession",
        return_value=session,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.reason is not None
    assert "SSL certificate error" in entry.reason
    assert "certificate has expired" in entry.reason
    assert not any(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))


async def test_update_expired_certificate(
    hass: HomeAssistant, mock_dsm: MagicMock
) -> None:
    """Test an update failing on an expired certificate."""
    entry = _entry()
    entry.add_to_hass(hass)
    with (
        patch(
            "homeassistant.components.synology_dsm.common.SynologyDSM",
            return_value=mock_dsm,
        ),
        patch("homeassistant.components.synology_dsm.PLATFORMS", []),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        coordinator = entry.runtime_data.coordinator_central

        mock_dsm.update.side_effect = SynologyDSMSSLException(_cert_expired_error())
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert "SSL certificate error" in str(coordinator.last_exception)
    assert "certificate has expired" in str(coordinator.last_exception)
    assert entry.state is ConfigEntryState.LOADED
    assert not any(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))


async def test_relogin_expired_certificate(
    hass: HomeAssistant, mock_dsm: MagicMock
) -> None:
    """Test a certificate error while re-logging in is an UpdateFailed."""
    entry = _entry()
    entry.add_to_hass(hass)
    with (
        patch(
            "homeassistant.components.synology_dsm.common.SynologyDSM",
            return_value=mock_dsm,
        ),
        patch("homeassistant.components.synology_dsm.PLATFORMS", []),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        coordinator = entry.runtime_data.coordinator_central

        mock_dsm.update.side_effect = SynologyDSMNotLoggedInException()
        mock_dsm.login.side_effect = SynologyDSMSSLException(_cert_expired_error())
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert "SSL certificate error" in str(coordinator.last_exception)
    assert not any(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
