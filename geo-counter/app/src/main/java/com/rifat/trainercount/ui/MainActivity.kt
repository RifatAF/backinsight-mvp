package com.rifat.trainercount.ui

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.geo.GeoSync
import com.rifat.trainercount.geo.Notifier
import com.rifat.trainercount.geo.Tracker

sealed interface Screen {
    data object Setup : Screen
    data object Home : Screen
    data object Stats : Screen
    data object Settings : Screen
    data class ClientView(val id: String) : Screen
    data class Edit(val id: String?) : Screen
}

class Nav(private val stack: SnapshotStateList<Screen>) {
    val current get() = stack.last()
    fun go(s: Screen) = stack.add(s)
    fun back() {
        if (stack.size > 1) stack.removeAt(stack.lastIndex)
    }
    fun reset(s: Screen) {
        stack.clear()
        stack.add(s)
    }
}

class MainActivity : ComponentActivity() {
    private val stack = mutableStateListOf<Screen>()
    private val nav = Nav(stack)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        Store.init(this)
        stack.add(if (Store.data.value.settings.setupDone) Screen.Home else Screen.Setup)
        openFrom(intent)
        setContent {
            AppTheme {
                val data by Store.data.collectAsStateWithLifecycle()
                BackHandler(enabled = stack.size > 1) { nav.back() }
                when (val s = nav.current) {
                    Screen.Setup -> SetupScreen(data, nav)
                    Screen.Home -> HomeScreen(data, nav)
                    Screen.Stats -> StatsScreen(data, nav)
                    Screen.Settings -> SettingsScreen(data, nav)
                    is Screen.ClientView -> ClientScreen(data, s.id, nav)
                    is Screen.Edit -> EditClientScreen(data, s.id, nav)
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        openFrom(intent)
    }

    private fun openFrom(i: Intent?) {
        val id = i?.getStringExtra(Notifier.EXTRA_CLIENT) ?: return
        i.removeExtra(Notifier.EXTRA_CLIENT)
        if (Store.data.value.settings.setupDone && nav.current != Screen.ClientView(id)) nav.go(Screen.ClientView(id))
    }

    override fun onResume() {
        super.onResume()
        GeoSync.sync(this)
        Tracker.checkWifiNow(this)
    }
}
