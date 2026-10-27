package com.rifat.trainercount

import android.app.Application
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.geo.Diag
import com.rifat.trainercount.geo.GeoSync
import com.rifat.trainercount.geo.Notifier
import com.rifat.trainercount.geo.Planner

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Store.init(this)
        Diag.loadState(this)
        Notifier.channels(this)
        Planner.channels(this)
        GeoSync.schedule(this)
        Planner.refresh(this)
    }
}
